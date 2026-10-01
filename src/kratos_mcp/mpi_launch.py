"""MPI launch helpers for simulation jobs. No Kratos import happens here.

`jobs.start` wraps the runner command in `mpiexec -n N ...` when `mpi_ranks`
is given. This module owns the launcher discovery, request validation, the
fail-before-launch capability probe, rank detection (used by the runner to
split per-rank logs) and process-group liveness helpers used by cancellation.

Environment:
  KRATOS_MPI_LAUNCHER  explicit launcher executable (default: mpiexec, then mpirun)
  KRATOS_MPI_ARGS      extra launcher flags, shell-split (e.g. "--oversubscribe")
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import Any, Mapping

# Environment variables the common launchers set for each rank.
_RANK_VARS = ("OMPI_COMM_WORLD_RANK", "PMIX_RANK", "PMI_RANK", "MV2_COMM_WORLD_RANK",
              "SLURM_PROCID")


def find_launcher() -> str | None:
    """Resolve the MPI launcher executable, or None when none is installed."""
    explicit = os.environ.get("KRATOS_MPI_LAUNCHER")
    if explicit:
        found = shutil.which(explicit)
        return found
    for name in ("mpiexec", "mpirun"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launcher_args() -> list[str]:
    return shlex.split(os.environ.get("KRATOS_MPI_ARGS", ""))


def build_command(launcher: str, ranks: int, inner: list[str]) -> list[str]:
    """`launcher [KRATOS_MPI_ARGS] -n ranks inner...`."""
    return [launcher, *launcher_args(), "-n", str(ranks), *inner]


def parallel_type(params: Any) -> str | None:
    """`problem_data.parallel_type` of parsed ProjectParameters (default OpenMP)."""
    if not isinstance(params, dict):
        return None
    problem = params.get("problem_data")
    if not isinstance(problem, dict):
        return "OpenMP"
    return str(problem.get("parallel_type", "OpenMP"))


def validate_request(params: Any, ranks: int | None, threads: int | None) -> None:
    """Reject inconsistent MPI/thread requests before anything is created.

    `params` is the parsed ProjectParameters, or None when it is not plain
    JSON (e.g. it carries `//` comments); the parallel_type cross-checks are
    skipped then because they cannot be evaluated.
    """
    for name, value in (("mpi_ranks", ranks), ("omp_threads", threads)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 1):
            raise ValueError(f"{name} must be a positive integer, got {value!r}")
    ptype = parallel_type(params)
    if ptype is None:
        return
    multistage = isinstance(params, dict) and "orchestrator" in params and "stages" in params
    if ranks is not None:
        if multistage:
            raise ValueError("mpi_ranks is not supported for multi-stage cases yet")
        if ptype != "MPI":
            raise ValueError(
                f"mpi_ranks={ranks} requires problem_data.parallel_type 'MPI' "
                f"(case has '{ptype}'); otherwise {ranks} copies of a serial run "
                "would write the same files")
    elif ptype == "MPI" and not multistage:
        raise ValueError(
            "problem_data.parallel_type is 'MPI': pass mpi_ranks to launch it under "
            "an MPI launcher")


def probe_kratos_mpi() -> dict[str, Any]:
    """Ask the Kratos build (in a worker subprocess) whether it has MPI support."""
    from . import bridge
    return bridge.run_op("mpi_support")


def check_capability(ranks: int) -> dict[str, Any]:
    """Fail before launch when no launcher or no MPI-enabled Kratos exists.

    Returns the launcher path and extra args to record in the manifest.
    """
    launcher = find_launcher()
    if launcher is None:
        hint = ("KRATOS_MPI_LAUNCHER=%r was not found on PATH" % os.environ["KRATOS_MPI_LAUNCHER"]
                if os.environ.get("KRATOS_MPI_LAUNCHER")
                else "install an MPI runtime (mpiexec/mpirun) or set KRATOS_MPI_LAUNCHER")
        raise RuntimeError(f"No MPI launcher available for mpi_ranks={ranks}: {hint}")
    try:
        support = probe_kratos_mpi()
    except Exception as exc:  # BridgeError and anything else: surface, never launch
        raise RuntimeError(f"Could not probe the Kratos build for MPI support: {exc}") from exc
    if not support.get("mpi_module"):
        raise RuntimeError(
            "This Kratos build has no MPI support (KratosMultiphysics.mpi is not "
            f"importable: {support.get('mpi_error', 'unknown error')})")
    return {"ranks": ranks, "launcher": launcher, "args": launcher_args(),
            "trilinos": bool(support.get("trilinos"))}


def rank_from_environ(environ: Mapping[str, str] | None = None) -> int | None:
    """MPI rank of the current process, or None when not launched by a launcher."""
    environ = os.environ if environ is None else environ
    for name in _RANK_VARS:
        value = environ.get(name)
        if value is not None and value.strip().lstrip("-").isdigit():
            return int(value)
    return None


def rank_log_path(log_dir: str | Path, rank: int) -> Path:
    return Path(log_dir) / f"rank-{rank}.log"


def group_members(pgid: int) -> list[int]:
    """Live (non-zombie) pids in a process group, from /proc (Linux)."""
    members: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return []
        except PermissionError:
            pass
        return [pgid]
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        # fields: state ppid pgrp ...
        if fields[0] != "Z" and int(fields[2]) == pgid:
            members.append(int(entry.name))
    return members


def group_alive(pgid: int) -> bool:
    return bool(group_members(pgid))
