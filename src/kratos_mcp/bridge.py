"""Bridge for short, synchronous operations that need a live Kratos.

Each call spawns `python -m kratos_mcp.worker <op>` in the Kratos
environment. The worker writes its JSON result to a temporary file
(--result-file) rather than stdout, so the Kratos import banner and any
solver chatter cannot corrupt the result. stdout/stderr are captured
and returned for diagnostics on failure.

Results are cached on disk keyed by (op, args, build fingerprint):
spawning a Kratos interpreter costs ~1-3 s and introspection results
only change when the build changes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import kratos_env

DEFAULT_TIMEOUT = 120.0

# Ops whose results depend only on the build, safe to cache.
_CACHEABLE_OPS = {"check", "list_variables", "list_applications", "get_solver_defaults"}


class BridgeError(RuntimeError):
    """A worker invocation failed. Carries captured output for diagnosis."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr

    def details(self) -> str:
        parts = [str(self)]
        if self.stderr.strip():
            parts.append("--- worker stderr (tail) ---\n" + self.stderr[-2000:])
        if self.stdout.strip():
            parts.append("--- worker stdout (tail) ---\n" + self.stdout[-2000:])
        return "\n".join(parts)


def _cache_path(env: kratos_env.KratosEnv, op: str, args: dict[str, Any]) -> Path:
    key = json.dumps({"op": op, "args": args, "build": env.fingerprint()}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    cache_dir = kratos_env.data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{op}-{digest}.json"


def run_op(
    op: str,
    args: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> Any:
    """Run a worker op inside the Kratos environment and return its result."""
    args = args or {}
    env = kratos_env.resolve()
    if not kratos_env.is_available(env):
        raise BridgeError(
            "Kratos is not available. Set KRATOS_ROOT to a Kratos checkout with a "
            "compiled build (bin/Release), or KRATOS_PYTHONPATH/KRATOS_LIBS explicitly."
        )

    cache_file = _cache_path(env, op, args) if op in _CACHEABLE_OPS and use_cache else None
    if cache_file is not None and cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass  # stale/corrupt cache entry; recompute

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="kratos-mcp-", delete=False
    ) as tf:
        result_file = Path(tf.name)
        json.dump({"op": op, "args": args}, tf)

    out_file = result_file.with_suffix(".out.json")
    try:
        proc = subprocess.run(
            [env.python, "-m", "kratos_mcp.worker",
             "--request-file", str(result_file), "--result-file", str(out_file)],
            env={**env.build_env(), "PYTHONPATH": _worker_pythonpath(env)},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if not out_file.exists():
            raise BridgeError(
                f"Worker op '{op}' produced no result (exit code {proc.returncode}).",
                stdout=proc.stdout, stderr=proc.stderr,
            )
        payload = json.loads(out_file.read_text())
        if not payload.get("ok"):
            raise BridgeError(
                f"Worker op '{op}' failed: {payload.get('error', 'unknown error')}",
                stdout=proc.stdout, stderr=proc.stderr,
            )
        result = payload["result"]
        if cache_file is not None:
            cache_file.write_text(json.dumps(result))
        return result
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"Worker op '{op}' timed out after {timeout}s.") from exc
    finally:
        for f in (result_file, out_file):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def _worker_pythonpath(env: kratos_env.KratosEnv) -> str:
    """PYTHONPATH that exposes both Kratos and this package to the worker."""
    own_pkg_root = str(Path(__file__).resolve().parent.parent)
    parts = [own_pkg_root]
    if env.pythonpath is not None:
        parts.append(str(env.pythonpath))
    existing = os.environ.get("PYTHONPATH")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def clear_cache() -> int:
    """Delete all cached bridge results. Returns number of files removed."""
    cache_dir = kratos_env.data_dir() / "cache"
    if not cache_dir.is_dir():
        return 0
    count = 0
    for f in cache_dir.glob("*.json"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count
