"""Job manager for long-running Kratos simulations.

Each job is a detached `python -m kratos_mcp.runner` subprocess with a
persistent directory under ~/.kratos-mcp/jobs/<job_id>/ holding:

  meta.json   - state machine + metadata (see JobMeta)
  manifest.json - launch provenance and input hashes
  snapshot/   - immutable inputs for isolated jobs
  execution/  - isolated solver working copy
  stdout.log  - combined stdout/stderr of the runner

State machine: queued -> running -> succeeded | failed | cancelled.
Because everything lives on disk, jobs survive MCP server restarts:
status is recomputed from pid liveness and the recorded return code."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    data.setdefault("extra", {})
    return JobMeta(**data)


def _write_meta(job_dir: Path, meta: JobMeta) -> None:
    tmp = job_dir / "meta.json.tmp"
    tmp.write_text(json.dumps(asdict(meta), indent=1))
    tmp.replace(job_dir / "meta.json")


_MANIFEST_VERSION = 1
_MANIFEST_ENV_KEYS = (
    "KRATOS_ROOT", "KRATOS_SOURCE", "KRATOS_PYTHONPATH", "KRATOS_LIBS",
    "KRATOS_EXTRA_LIBS", "PATH", "PYTHONPATH", "LD_LIBRARY_PATH",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS",
)
_EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".cache",
    ".tox", "htmlcov", "node_modules", "jobs", "job_storage", ".kratos-mcp",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_destination(value: str, *, key: str) -> str:
    """Normalize a case-relative Kratos filename without changing its suffix."""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{key} must stay inside an isolated case: {value!r}")
    return path.as_posix()


def _rewrite_references(value: Any, case: Path, external: dict[Path, str],
                        copied_external: dict[str, Path], key: str = "") -> Any:
    """Rewrite mesh/material references while preserving all other JSON."""
    if isinstance(value, dict):
        return {k: _rewrite_references(v, case, external, copied_external, k)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_references(v, case, external, copied_external, key) for v in value]
    if not isinstance(value, str) or key not in {"input_filename", "materials_filename"}:
        return value

    source = Path(value).expanduser()
    if not source.is_absolute():
        source = case / source
    candidate = source.resolve(strict=False)
    if key == "input_filename" and not candidate.is_file() and candidate.with_suffix(".mdpa").is_file():
        candidate = candidate.with_suffix(".mdpa")
    if key == "input_filename" and candidate not in external and candidate.with_suffix(".mdpa") in external:
        candidate = candidate.with_suffix(".mdpa")

    try:
        candidate.relative_to(case)
    except ValueError:
        mapped = external.get(candidate)
        if mapped is None:
            raise ValueError(f"external {key} {value!r} requires an external_inputs mapping")
        destination = _relative_destination(mapped, key="external_inputs destination")
        if key == "input_filename" and candidate.suffix == ".mdpa" and not destination.endswith(".mdpa"):
            destination += ".mdpa"
        if destination in copied_external and copied_external[destination] != candidate:
            raise ValueError(f"external input destinations collide: {destination}")
        copied_external[destination] = candidate
        return destination[:-5] if key == "input_filename" and destination.endswith(".mdpa") and Path(value).suffix != ".mdpa" else destination
    if not candidate.is_file():
        raise ValueError(f"{key} file does not exist: {value!r}")
    relative = candidate.relative_to(case).as_posix()
    if key == "input_filename" and relative.endswith(".mdpa") and Path(value).suffix != ".mdpa":
        return relative[:-5]
    return relative


def _validate_output_paths(value: Any, *, key: str = "") -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            _validate_output_paths(child, key=name)
    elif isinstance(value, list):
        for child in value:
            _validate_output_paths(child, key=key)
    elif isinstance(value, str) and key in {"output_path", "output_directory", "output_file_name"}:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"isolated output path must stay inside the case: {value!r}")


def _case_inputs(case: Path, *, strict_symlinks: bool = True,
                 excluded_dirs: set[str] | None = None,
                 allowed_external: set[Path] | None = None) -> list[Path]:
    files: list[Path] = []
    output_dirs = {Path(item).as_posix() for item in (excluded_dirs or set())}
    for root, dirs, names in os.walk(case, followlinks=False):
        root_path = Path(root)
        kept_dirs: list[str] = []
        for name in dirs:
            directory = root_path / name
            if directory.is_symlink():
                if strict_symlinks:
                    raise ValueError(
                        f"directory symlink is not allowed in an isolated case: {directory}")
                continue
            if name in _EXCLUDED_DIRS:
                continue
            relative = directory.relative_to(case).as_posix()
            if any(relative == output or relative.startswith(output + "/")
                   for output in output_dirs):
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in names:
            source = root_path / name
            if source.is_symlink():
                target = source.resolve(strict=False)
                if not target.is_file() and strict_symlinks:
                    raise ValueError(f"directory or broken symlink is not allowed in an isolated case: {source}")
                try:
                    target.relative_to(case)
                except ValueError:
                    if strict_symlinks and target not in (allowed_external or set()):
                        raise ValueError(f"external symlink {source} requires an external_inputs mapping")
            if source.is_file():
                files.append(source)
    return files


def _configured_output_dirs(params: Any) -> set[str]:
    output_dirs: set[str] = set()

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                collect(child, name)
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and key in {"output_path", "output_directory"}:
            path = Path(value)
            if not path.is_absolute() and path.parts and ".." not in path.parts:
                output_dirs.add(path.as_posix())

    collect(params)
    return output_dirs


def _read_case_parameters(case: Path, parameters_file: str) -> Any | None:
    try:
        return json.loads((case / parameters_file).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _prepare_isolated_case(case: Path, parameters_file: str, job_dir: Path,
                           external_inputs: dict[str, str] | None) -> tuple[Path, Path, dict[str, str]]:
    external: dict[Path, str] = {}
    for src, dest in (external_inputs or {}).items():
        source = Path(src).expanduser()
        if not source.is_absolute():
            raise ValueError(f"external input source must be absolute: {src!r}")
        source = source.resolve()
        if not source.is_file():
            raise ValueError(f"external input is not a regular file: {source}")
        destination = _relative_destination(dest, key="external_inputs destination")
        target = case / destination
        try:
            target.relative_to(case)
        except ValueError as exc:  # defensive: _relative_destination already checks this
            raise ValueError(f"external_inputs destination escapes the case: {dest!r}") from exc
        if target.exists() or target.is_symlink():
            raise ValueError(f"external input destination collides with case file: {destination}")
        if destination in external.values():
            raise ValueError(f"external input destinations collide: {destination}")
        external[source] = destination
    snapshot = job_dir / "snapshot"
    execution = job_dir / "execution"
    snapshot.mkdir()
    copied_external: dict[str, Path] = {}
    parameter_path = case / parameters_file
    try:
        params = json.loads(parameter_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot snapshot {parameters_file}: {exc}") from exc
    _validate_output_paths(params)
    source_files = _case_inputs(case, excluded_dirs=_configured_output_dirs(params),
                                allowed_external=set(external))
    for source in source_files:
        relative = source.relative_to(case)
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if _sha256(source) != _sha256(destination):
            raise RuntimeError(f"input changed while creating snapshot: {source}")

    rewritten = _rewrite_references(params, case, external, copied_external)
    for destination, source in copied_external.items():
        target = snapshot / destination
        if target.exists() or target.is_symlink():
            raise ValueError(f"external input destination collides with case file: {destination}")
        if not source.is_file():
            raise ValueError(f"external input is not a regular file: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if _sha256(source) != _sha256(target):
            raise RuntimeError(f"input changed while creating snapshot: {source}")
    (snapshot / parameters_file).write_text(json.dumps(rewritten, indent=2) + "\n")
    shutil.copytree(snapshot, execution)
    hashes = {str(p.relative_to(snapshot)): _sha256(p) for p in snapshot.rglob("*") if p.is_file()}
    return snapshot, execution, hashes


def _manifest(env: kratos_env.KratosEnv, *, case: Path, execution: Path,
              parameters_file: str, command: list[str], isolate: bool,
              hashes: dict[str, str] | None, analysis_type: str | None,
              analysis_class: str | None,
              external_inputs: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "version": _MANIFEST_VERSION,
        "created_at": time.time(),
        "original_case_dir": str(case),
        "execution_case_dir": str(execution),
        "parameters_file": parameters_file,
        "isolate": isolate,
        "analysis_type": analysis_type,
        "analysis_class": analysis_class,
        "analysis_overrides": {
            "analysis_type": analysis_type,
            "analysis_class": analysis_class,
        },
        "external_inputs": {str(source): str(destination)
                            for source, destination in (external_inputs or {}).items()},
        "command": command,
        "python": env.python,
        "python_executable": env.python,
        "kratos_fingerprint": env.fingerprint(),
        "environment": {key: os.environ[key] for key in _MANIFEST_ENV_KEYS if key in os.environ},
        "input_hashes": hashes or {},
    }


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
    isolate: bool = False,
    external_inputs: dict[str, str] | None = None,
) -> JobMeta:
    """Spawn a detached runner and return the initial job metadata."""
    env = kratos_env.resolve()
    if not kratos_env.is_available(env):
        raise RuntimeError("Kratos is not available; cannot start a simulation.")

    case = Path(case_dir).expanduser().resolve()
    if not (case / parameters_file).is_file():
        raise FileNotFoundError(f"{case / parameters_file} does not exist")

    if external_inputs and not isolate:
        raise ValueError("external_inputs requires isolate=True")

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job_dir = jobs_root() / job_id
    job_dir.mkdir(parents=True)

    snapshot: Path | None = None
    execution = case
    try:
        if isolate:
            snapshot = job_dir / "snapshot"
            execution = job_dir / "execution"
            snapshot, execution, hashes = _prepare_isolated_case(
                case, parameters_file, job_dir, external_inputs)
        else:
            snapshot, execution, hashes = None, case, {
                str(path.relative_to(case)): _sha256(path)
                for path in _case_inputs(
                    case, strict_symlinks=False,
                    excluded_dirs=_configured_output_dirs(_read_case_parameters(case, parameters_file) or {}))
            }
        cmd = [env.python, "-u", "-m", "kratos_mcp.runner",
               "--case-dir", str(execution), "--parameters", parameters_file]
        if analysis_type:
            cmd += ["--analysis-type", analysis_type]
        if analysis_class:
            cmd += ["--analysis-class", analysis_class]
        manifest = _manifest(env, case=case, execution=execution,
                             parameters_file=parameters_file, command=cmd,
                             isolate=isolate, hashes=hashes,
                             analysis_type=analysis_type, analysis_class=analysis_class,
                             external_inputs=external_inputs)
        (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError) as exc:
        meta = JobMeta(
            job_id=job_id, case_dir=str(case), parameters_file=parameters_file,
            state="failed", returncode=None, created_at=time.time(),
            finished_at=time.time(), analysis_type=analysis_type,
            extra={"preparation_error": str(exc),
                   "original_case_dir": str(case),
                   "execution_case_dir": str(execution),
                   "snapshot_dir": str(snapshot) if snapshot else None,
                   "manifest": str(job_dir / "manifest.json")},
        )
        _write_meta(job_dir, meta)
        raise RuntimeError(f"Could not prepare simulation case: {exc}") from exc

    own_pkg_root = str(Path(__file__).resolve().parent.parent)
    run_env = env.build_env()
    run_env["PYTHONPATH"] = own_pkg_root + os.pathsep + run_env.get("PYTHONPATH", "")

    log = open(job_dir / "stdout.log", "wb")
    try:
        proc = subprocess.Popen(
            cmd, env=run_env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        meta = JobMeta(
            job_id=job_id, case_dir=str(execution), parameters_file=parameters_file,
            state="failed", returncode=None, created_at=time.time(),
            finished_at=time.time(), analysis_type=analysis_type,
            extra={"launch_error": str(exc),
                   "original_case_dir": str(case),
                   "execution_case_dir": str(execution),
                   "snapshot_dir": str(snapshot) if snapshot else None,
                   "manifest": str(job_dir / "manifest.json")},
        )
        _write_meta(job_dir, meta)
        raise RuntimeError(f"Could not launch simulation: {exc}") from exc
    finally:
        log.close()

    meta = JobMeta(
        job_id=job_id, case_dir=str(execution), parameters_file=parameters_file,
        state="running", pid=proc.pid, created_at=time.time(),
        started_at=time.time(), analysis_type=analysis_type,
        extra={"original_case_dir": str(case), "execution_case_dir": str(execution),
               "snapshot_dir": str(snapshot) if snapshot else None,
               "analysis_class": analysis_class,
               "manifest": str(job_dir / "manifest.json")},
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


def rerun(job_id: str) -> JobMeta:
    """Launch a fresh isolated job from a preserved, verified snapshot."""
    old_dir = _job_dir(job_id)
    old = _read_meta(old_dir)
    snapshot_name = old.extra.get("snapshot_dir")
    if not snapshot_name:
        raise RuntimeError(f"Job '{job_id}' has no preserved input snapshot")
    snapshot = Path(snapshot_name)
    manifest_path = old_dir / "manifest.json"
    if not snapshot.is_dir() or not manifest_path.is_file():
        raise RuntimeError(f"Job '{job_id}' snapshot is incomplete")
    manifest = json.loads(manifest_path.read_text())
    env = kratos_env.resolve()
    if manifest.get("kratos_fingerprint") != env.fingerprint():
        raise RuntimeError("Kratos build fingerprint changed; refusing to rerun the snapshot")
    for relative, expected in manifest.get("input_hashes", {}).items():
        path = snapshot / relative
        if not path.is_file() or _sha256(path) != expected:
            raise RuntimeError(f"Snapshot input changed or is missing: {relative}")
    new = start(str(snapshot), old.parameters_file,
                old.analysis_type or manifest.get("analysis_type"),
                old.extra.get("analysis_class") or manifest.get("analysis_class"),
                isolate=True)
    new_dir = _job_dir(new.job_id)
    new.extra["rerun_of"] = job_id
    new.extra["original_case_dir"] = manifest.get("original_case_dir", old.case_dir)
    new_manifest_path = new_dir / "manifest.json"
    new_manifest = json.loads(new_manifest_path.read_text())
    new_manifest["original_case_dir"] = manifest.get("original_case_dir", old.case_dir)
    new_manifest["rerun_of"] = job_id
    new_manifest["external_inputs"] = manifest.get("external_inputs", {})
    new_manifest_path.write_text(json.dumps(new_manifest, indent=2) + "\n")
    _write_meta(new_dir, new)
    return new


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
