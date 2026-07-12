"""Job manager for long-running Kratos simulations.

Each job is a detached `python -m kratos_mcp.runner` subprocess with a
persistent directory under ~/.kratos-mcp/jobs/<job_id>/ holding:

  meta.json   - state machine + metadata (see JobMeta)
  stdout.log  - combined stdout/stderr of the runner

State machine: queued -> running -> succeeded | failed | cancelled.
Because everything lives on disk, jobs survive MCP server restarts:
status is recomputed from pid liveness and the recorded return code."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import kratos_env

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


@dataclass
class JobMeta:
    job_id: str
    case_dir: str
    parameters_file: str
    state: str = "queued"
    pid: int | None = None
    returncode: int | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    analysis_type: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def jobs_root() -> Path:
    root = kratos_env.data_dir() / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_dir(job_id: str) -> Path:
    d = jobs_root() / job_id
    if not d.is_dir():
        raise KeyError(f"Unknown job '{job_id}'")
    return d


def _read_meta(job_dir: Path) -> JobMeta:
    data = json.loads((job_dir / "meta.json").read_text())
    return JobMeta(**data)


def _write_meta(job_dir: Path, meta: JobMeta) -> None:
    tmp = job_dir / "meta.json.tmp"
    tmp.write_text(json.dumps(asdict(meta), indent=1))
    tmp.replace(job_dir / "meta.json")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start(
    case_dir: str,
    parameters_file: str = "ProjectParameters.json",
    analysis_type: str | None = None,
    analysis_class: str | None = None,
) -> JobMeta:
    """Spawn a detached runner and return the initial job metadata."""
    env = kratos_env.resolve()
    if not kratos_env.is_available(env):
        raise RuntimeError("Kratos is not available; cannot start a simulation.")

    case = Path(case_dir).expanduser().resolve()
    if not (case / parameters_file).is_file():
        raise FileNotFoundError(f"{case / parameters_file} does not exist")

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job_dir = jobs_root() / job_id
    job_dir.mkdir(parents=True)

    cmd = [env.python, "-u", "-m", "kratos_mcp.runner",
           "--case-dir", str(case), "--parameters", parameters_file]
    if analysis_type:
        cmd += ["--analysis-type", analysis_type]
    if analysis_class:
        cmd += ["--analysis-class", analysis_class]

    own_pkg_root = str(Path(__file__).resolve().parent.parent)
    run_env = env.build_env()
    run_env["PYTHONPATH"] = own_pkg_root + os.pathsep + run_env.get("PYTHONPATH", "")

    log = open(job_dir / "stdout.log", "wb")
    proc = subprocess.Popen(
        cmd, env=run_env, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    log.close()

    meta = JobMeta(
        job_id=job_id, case_dir=str(case), parameters_file=parameters_file,
        state="running", pid=proc.pid, created_at=time.time(),
        started_at=time.time(), analysis_type=analysis_type,
    )
    _write_meta(job_dir, meta)
    # Keep a handle so the child is reaped when it exits (best effort:
    # if the server restarts, pid-liveness polling takes over).
    _live_procs[job_id] = proc
    return meta


_live_procs: dict[str, subprocess.Popen] = {}


def refresh(job_id: str) -> JobMeta:
    """Re-evaluate and persist the job state from process liveness."""
    job_dir = _job_dir(job_id)
    meta = _read_meta(job_dir)
    if meta.state in TERMINAL_STATES:
        return meta

    proc = _live_procs.get(job_id)
    returncode: int | None = None
    if proc is not None:
        returncode = proc.poll()
        finished = returncode is not None
    else:
        finished = meta.pid is None or not _pid_alive(meta.pid)
        # Without the Popen handle the return code is unknown; infer
        # success from the runner's final log line.
        if finished:
            returncode = 0 if _log_indicates_success(job_dir) else 1

    if finished:
        meta.returncode = returncode
        meta.finished_at = time.time()
        meta.state = "succeeded" if returncode == 0 else "failed"
        _write_meta(job_dir, meta)
        _live_procs.pop(job_id, None)
    return meta


def _log_indicates_success(job_dir: Path) -> bool:
    """Heuristic for orphaned jobs: AnalysisStage prints an end banner."""
    try:
        tail = (job_dir / "stdout.log").read_bytes()[-4000:].decode(errors="replace")
    except OSError:
        return False
    return "Analysis -END-" in tail or "ANALYSIS COMPLETED" in tail.upper()


def status(job_id: str) -> dict[str, Any]:
    meta = refresh(job_id)
    out = asdict(meta)
    if meta.started_at:
        end = meta.finished_at or time.time()
        out["elapsed_seconds"] = round(end - meta.started_at, 1)
    return out


def list_jobs(state: str | None = None) -> list[dict[str, Any]]:
    results = []
    for job_dir in sorted(jobs_root().iterdir()):
        if not (job_dir / "meta.json").is_file():
            continue
        try:
            meta = refresh(job_dir.name)
        except (KeyError, json.JSONDecodeError):
            continue
        if state is None or meta.state == state:
            results.append(asdict(meta))
    return results


def logs(job_id: str, tail: int = 100, grep: str | None = None) -> str:
    job_dir = _job_dir(job_id)
    try:
        text = (job_dir / "stdout.log").read_text(errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if grep:
        lines = [ln for ln in lines if grep.lower() in ln.lower()]
    if tail > 0:
        lines = lines[-tail:]
    return "\n".join(lines)


def log_path(job_id: str) -> Path:
    return _job_dir(job_id) / "stdout.log"


def cancel(job_id: str, grace_seconds: float = 5.0) -> dict[str, Any]:
    job_dir = _job_dir(job_id)
    meta = refresh(job_id)
    if meta.state in TERMINAL_STATES:
        return asdict(meta)
    if meta.pid is not None:
        try:
            # The runner leads its own session; signal the whole group.
            os.killpg(meta.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + grace_seconds
        while time.time() < deadline and _pid_alive(meta.pid):
            time.sleep(0.2)
        if _pid_alive(meta.pid):
            try:
                os.killpg(meta.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    meta.state = "cancelled"
    meta.finished_at = time.time()
    _write_meta(job_dir, meta)
    _live_procs.pop(job_id, None)
    return asdict(meta)
