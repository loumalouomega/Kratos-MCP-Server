"""Interactive Kratos sessions: long-lived `session_worker` subprocesses that
keep a live Model + AnalysisStage in memory and are driven step by step.

This module never imports Kratos. It spawns `python -m kratos_mcp.session_worker`
(the only Kratos-importing piece) with the Kratos environment injected and
talks to it over JSON lines: requests on the worker's stdin, responses on its
stdout. The worker re-points its fds 1/2 at `session.log`, so the Kratos banner
and solver output can never corrupt the response channel.

State lives in `KRATOS_MCP_HOME/sessions/<id>/` (meta.json, session.log,
history.jsonl). Sessions are process-bound: they do NOT survive a server
restart (a persisted meta whose process is gone is reported as `lost`)."""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import jobs, kratos_env, mpi_launch

DEFAULT_MAX_SESSIONS = 4
DEFAULT_START_TIMEOUT = 300.0
DEFAULT_REQUEST_TIMEOUT = 600.0
LOG_TAIL_LINES = 40
WORKER_MODULE = "kratos_mcp.session_worker"  # tests substitute a fake line-protocol worker

ACTIVE_STATES = {"starting", "ready"}
TERMINAL_STATES = {"closed", "dead", "lost"}


def sessions_root() -> Path:
    root = kratos_env.data_dir() / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def max_sessions() -> int:
    try:
        return max(1, int(os.environ.get("KRATOS_MCP_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)))
    except ValueError:
        return DEFAULT_MAX_SESSIONS


def exec_enabled() -> bool:
    return os.environ.get("KRATOS_MCP_ALLOW_EXEC", "") in ("1", "true", "yes")


class SessionError(RuntimeError):
    """A session operation failed (message is safe to return to the client)."""


@dataclass
class SessionMeta:
    session_id: str
    case_dir: str
    parameters_file: str
    state: str = "starting"
    pid: int | None = None
    returncode: int | None = None
    created_at: float = field(default_factory=time.time)
    closed_at: float | None = None
    analysis_class: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _session_dir(session_id: str) -> Path:
    path = sessions_root() / session_id
    if not path.is_dir():
        raise KeyError(f"No such session: {session_id}")
    return path


def _write_meta(sdir: Path, meta: SessionMeta) -> None:
    tmp = sdir / f"meta.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(asdict(meta), indent=1))
    tmp.replace(sdir / "meta.json")


def _read_meta(sdir: Path) -> SessionMeta:
    return SessionMeta(**json.loads((sdir / "meta.json").read_text()))


def _tail(path: Path, lines: int = LOG_TAIL_LINES) -> str:
    try:
        return "".join(path.read_text(errors="replace").splitlines(keepends=True)[-lines:])
    except OSError:
        return ""


class _Live:
    """The in-process half of a session: the child process and its channel."""

    def __init__(self, meta: SessionMeta, sdir: Path, proc: subprocess.Popen) -> None:
        self.meta = meta
        self.dir = sdir
        self.proc = proc
        self.cond = threading.Condition()
        self.responses: dict[int, dict[str, Any]] = {}
        self.eof = False
        self.next_id = 1
        self.pending: list[int] = []  # ids sent but not yet answered
        self.send_lock = threading.Lock()
        self.reader = threading.Thread(target=self._read_loop, daemon=True,
                                       name=f"session-{meta.session_id}")
        self.reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # stray non-protocol output; ignore
            with self.cond:
                rid = int(message.get("id", -1))
                if rid in self.pending:  # answered, even if its caller already timed out
                    self.pending.remove(rid)
                self.responses[rid] = message
                self.cond.notify_all()
        with self.cond:
            self.eof = True
            self.cond.notify_all()

    def wait_for(self, request_id: int, timeout: float) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        with self.cond:
            while request_id not in self.responses:
                if self.eof:
                    return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError
                self.cond.wait(remaining)
            return self.responses.pop(request_id)


_live: dict[str, _Live] = {}
_lock = threading.RLock()


def _mark_dead(live: _Live, reason: str) -> None:
    meta = live.meta
    if meta.state in TERMINAL_STATES:
        return
    try:
        rc = live.proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        rc = None
    meta.state = "dead"
    meta.returncode = rc
    meta.closed_at = time.time()
    meta.extra["death_reason"] = reason
    meta.extra["log_tail"] = _tail(live.dir / "session.log")
    _write_meta(live.dir, meta)


def _active_count() -> int:
    with _lock:
        return sum(1 for live in _live.values()
                   if live.meta.state in ACTIVE_STATES and live.proc.poll() is None)


def start(case_dir: str, parameters_file: str = "ProjectParameters.json",
          analysis_type: str | None = None, analysis_class: str | None = None,
          isolate: bool = False, timeout: float = DEFAULT_START_TIMEOUT) -> dict[str, Any]:
    """Spawn a session worker, wait for its ready handshake and return the
    session status. Raises SessionError on any failure."""
    case = Path(case_dir).expanduser().resolve()
    if not case.is_dir():
        raise SessionError(f"Case directory not found: {case}")
    if not (case / parameters_file).is_file():
        raise SessionError(f"Parameters file not found: {case / parameters_file}")
    env = kratos_env.resolve()
    if not kratos_env.is_available(env):
        raise SessionError("Kratos is not available. Set KRATOS_ROOT or run kratos_install.")
    limit = max_sessions()
    if _active_count() >= limit:
        raise SessionError(f"Too many open sessions (limit {limit}, KRATOS_MCP_MAX_SESSIONS). "
                           "Close one with session_close first.")

    session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    sdir = sessions_root() / session_id
    sdir.mkdir(parents=True)
    work = case
    if isolate:
        work = sdir / "execution"
        shutil.copytree(case, work)
    meta = SessionMeta(session_id=session_id, case_dir=str(work),
                       parameters_file=parameters_file,
                       extra={"original_case_dir": str(case), "isolated": isolate})
    _write_meta(sdir, meta)

    cmd = [env.python, "-u", "-m", WORKER_MODULE,
           "--case-dir", str(work), "--parameters", parameters_file,
           "--log-file", str(sdir / "session.log")]
    if analysis_type:
        cmd += ["--analysis-type", analysis_type]
    if analysis_class:
        cmd += ["--analysis-class", analysis_class]
    run_env = env.build_env()
    run_env["PYTHONPATH"] = (str(Path(__file__).resolve().parent.parent)
                             + os.pathsep + run_env.get("PYTHONPATH", ""))
    try:
        proc = subprocess.Popen(cmd, env=run_env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1, start_new_session=True)
    except OSError as exc:
        meta.state = "dead"
        meta.extra["launch_error"] = str(exc)
        _write_meta(sdir, meta)
        raise SessionError(f"Could not launch the session worker: {exc}") from exc

    meta.pid = proc.pid
    live = _Live(meta, sdir, proc)
    with _lock:
        _live[session_id] = live
    _write_meta(sdir, meta)

    try:
        ready = live.wait_for(0, timeout)
    except TimeoutError:
        _terminate(live)
        meta.state = "dead"
        meta.extra["death_reason"] = f"no ready handshake within {timeout:.0f}s"
        meta.extra["log_tail"] = _tail(sdir / "session.log")
        meta.closed_at = time.time()
        _write_meta(sdir, meta)
        raise SessionError(f"Session did not become ready within {timeout:.0f}s.\n"
                           + meta.extra["log_tail"])
    if ready is None or not ready.get("ok"):
        error = (ready or {}).get("error") or "worker exited during startup"
        _mark_dead(live, f"startup failed: {error}")
        raise SessionError(f"Session failed to start: {error}\n{meta.extra.get('log_tail', '')}")

    meta.state = "ready"
    meta.analysis_class = ready["result"].get("analysis_class")
    _write_meta(sdir, meta)
    return {"session_id": session_id, "state": "ready", "case_dir": str(work),
            "log_file": str(sdir / "session.log"), **ready["result"]}


def request(session_id: str, op: str, args: dict[str, Any] | None = None,
            timeout: float = DEFAULT_REQUEST_TIMEOUT) -> dict[str, Any]:
    """Send one op to a live session and return its result.

    If the worker is still busy when `timeout` expires the request keeps
    running; the raised SessionError carries busy=True and the request id so
    the caller can poll with session_status."""
    live = _get_live(session_id)
    if live.meta.state not in ACTIVE_STATES:
        raise SessionError(f"Session {session_id} is {live.meta.state}.")
    with live.cond:
        request_id = live.next_id
        live.next_id += 1
        live.pending.append(request_id)
    payload = json.dumps({"id": request_id, "op": op, "args": args or {}}) + "\n"
    try:
        with live.send_lock:
            assert live.proc.stdin is not None
            live.proc.stdin.write(payload)
            live.proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        _mark_dead(live, "worker pipe closed")
        raise SessionError(f"Session {session_id} died.\n{live.meta.extra.get('log_tail', '')}")
    try:
        response = live.wait_for(request_id, timeout)
    except TimeoutError:
        err = SessionError(f"Session {session_id} is still busy with '{op}' after {timeout:.0f}s; "
                           "it keeps running. Check session_status or retry later.")
        err.busy = True  # type: ignore[attr-defined]
        raise err
    if response is None:
        _mark_dead(live, "worker exited unexpectedly")
        raise SessionError(f"Session {session_id} died (exit code {live.meta.returncode}).\n"
                           f"{live.meta.extra.get('log_tail', '')}")
    if not response.get("ok"):
        err = SessionError(response.get("error", "unknown error"))
        err.traceback = response.get("traceback", "")  # type: ignore[attr-defined]
        raise err
    return response["result"]


def record_history(session_id: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    with open(_session_dir(session_id) / "history.jsonl", "a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def history(session_id: str) -> list[dict[str, Any]]:
    path = _session_dir(session_id) / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _get_live(session_id: str) -> _Live:
    with _lock:
        live = _live.get(session_id)
    if live is None:
        meta = _read_meta(_session_dir(session_id))  # KeyError if unknown
        raise SessionError(f"Session {session_id} is not live in this server "
                           f"(state: {_effective_state(meta)}); sessions do not survive a restart.")
    if live.meta.state in ACTIVE_STATES and live.proc.poll() is not None:
        _mark_dead(live, f"worker exited with code {live.proc.returncode}")
    return live


def _effective_state(meta: SessionMeta) -> str:
    if meta.state in ACTIVE_STATES:
        with _lock:
            if meta.session_id not in _live:
                return "lost"
    return meta.state


def status(session_id: str) -> dict[str, Any]:
    sdir = _session_dir(session_id)
    with _lock:
        live = _live.get(session_id)
    if live is not None:
        if live.meta.state in ACTIVE_STATES and live.proc.poll() is not None:
            _mark_dead(live, f"worker exited with code {live.proc.returncode}")
        meta = live.meta
    else:
        meta = _read_meta(sdir)
        if _effective_state(meta) == "lost":
            meta.state = "lost"
    out: dict[str, Any] = {
        "session_id": session_id, "state": meta.state, "pid": meta.pid,
        "case_dir": meta.case_dir, "analysis_class": meta.analysis_class,
        "age_seconds": round(time.time() - meta.created_at, 1),
        "log_file": str(sdir / "session.log"),
        "steps_recorded": len(history(session_id)),
    }
    if live is not None:
        out["busy"] = bool(live.pending)
    if meta.returncode is not None:
        out["returncode"] = meta.returncode
    for key in ("death_reason", "log_tail", "isolated", "original_case_dir"):
        if key in meta.extra:
            out[key] = meta.extra[key]
    return out


def list_sessions(state: str | None = None) -> list[dict[str, Any]]:
    out = []
    for sdir in sorted(sessions_root().iterdir(), reverse=True):
        if not (sdir / "meta.json").exists():
            continue
        try:
            item = status(sdir.name)
        except (KeyError, json.JSONDecodeError, TypeError):
            continue
        if state is None or item["state"] == state:
            out.append({k: item[k] for k in ("session_id", "state", "case_dir",
                                             "analysis_class", "age_seconds")})
    return out


def logs(session_id: str, tail: int = 100) -> str:
    return _tail(_session_dir(session_id) / "session.log", tail)


def process_group_alive(pgid: int) -> bool:
    return mpi_launch.group_alive(pgid)


def _terminate(live: _Live, grace: float = 3.0) -> None:
    pgid = live.proc.pid
    jobs._kill_group(pgid, grace)
    try:
        live.proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def close(session_id: str, finalize: bool = True, timeout: float = 120.0) -> dict[str, Any]:
    """Close a session: ask the worker to finalize the AnalysisStage (flushes
    output processes), then make sure its process group is gone."""
    sdir = _session_dir(session_id)
    with _lock:
        live = _live.get(session_id)
    if live is None:
        meta = _read_meta(sdir)
        return {"session_id": session_id, "state": _effective_state(meta),
                "finalized": False, "note": "session was not live"}
    finalized = False
    error = None
    if finalize and live.meta.state in ACTIVE_STATES and live.proc.poll() is None:
        try:
            request(session_id, "finalize", {}, timeout=timeout)
            finalized = True
        except SessionError as exc:
            error = str(exc)
    if live.proc.poll() is None:
        try:
            live.proc.wait(timeout=5.0 if finalized else 0.1)
        except subprocess.TimeoutExpired:
            pass
    _terminate(live)
    if live.meta.state not in TERMINAL_STATES:
        live.meta.state = "closed"
        live.meta.returncode = live.proc.returncode
        live.meta.closed_at = time.time()
        live.meta.extra["finalized"] = finalized
        _write_meta(sdir, live.meta)
    with _lock:
        _live.pop(session_id, None)
    out = {"session_id": session_id, "state": live.meta.state, "finalized": finalized}
    if error:
        out["finalize_error"] = error
    return out


def close_all() -> None:
    for session_id in list(_live):
        try:
            close(session_id, finalize=False)
        except Exception:  # noqa: BLE001 - best effort at interpreter exit
            pass


atexit.register(close_all)

