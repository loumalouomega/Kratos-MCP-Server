"""MPI launch helpers and job lifecycle, exercised with a fake launcher.

No MPI runtime or Kratos build is needed: the fake `mpiexec` is a Python
script that records its arguments and spawns sleeping "ranks" in the job's
process group, which is all the lifecycle code (cancel, stray-rank cleanup)
observes. Real distributed runs are covered by test_mpi_integration.py.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from kratos_mcp import jobs, kratos_env, mpi_launch


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("KRATOS_MPI_ARGS", raising=False)
    monkeypatch.delenv("KRATOS_MPI_LAUNCHER", raising=False)


@pytest.fixture
def fake_env(monkeypatch):
    env = kratos_env.KratosEnv(root=None, pythonpath=None, libs=None, source=None,
                               pip_installed=True, python=sys.executable)
    monkeypatch.setattr(jobs.kratos_env, "resolve", lambda: env)
    monkeypatch.setattr(mpi_launch, "probe_kratos_mpi",
                        lambda: {"mpi_module": True, "trilinos": True})
    return env


@pytest.fixture
def fake_launcher(tmp_path, monkeypatch):
    """A `mpiexec` stand-in: records argv/env and spawns N rank processes."""
    record = tmp_path / "launch.json"
    script = tmp_path / "fake_mpiexec"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, subprocess, sys
        argv = sys.argv[1:]
        n = int(argv[argv.index("-n") + 1])
        json.dump({{"argv": argv, "omp": os.environ.get("OMP_NUM_THREADS"),
                   "mkl": os.environ.get("MKL_NUM_THREADS")}}, open({str(record)!r}, "w"))
        # Ranks ignore SIGTERM so cancellation has to escalate to SIGKILL.
        ranks = [subprocess.Popen(["sh", "-c", 'trap "" TERM; sleep 60']) for _ in range(n)]
        if os.environ.get("FAKE_MPI_DETACH"):
            sys.exit(0)   # launcher dies, ranks linger
        for r in ranks:
            r.wait()
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KRATOS_MPI_LAUNCHER", str(script))
    return record


def _case(tmp_path: Path, parallel_type: str = "MPI", extra: dict | None = None) -> Path:
    case = tmp_path / "case"
    case.mkdir(exist_ok=True)
    params = {"problem_data": {"parallel_type": parallel_type}, **(extra or {})}
    (case / "ProjectParameters.json").write_text(json.dumps(params))
    return case


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------- pure helpers

def test_find_launcher_prefers_env_override(fake_launcher):
    assert mpi_launch.find_launcher() == str(fake_launcher.parent / "fake_mpiexec")


def test_find_launcher_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert mpi_launch.find_launcher() is None
    monkeypatch.setenv("KRATOS_MPI_LAUNCHER", "no-such-launcher")
    assert mpi_launch.find_launcher() is None


def test_build_command_includes_extra_args(monkeypatch):
    monkeypatch.setenv("KRATOS_MPI_ARGS", "--oversubscribe --bind-to none")
    assert mpi_launch.build_command("/usr/bin/mpiexec", 4, ["python", "-m", "x"]) == [
        "/usr/bin/mpiexec", "--oversubscribe", "--bind-to", "none", "-n", "4",
        "python", "-m", "x"]


@pytest.mark.parametrize("params,ranks,threads,message", [
    ({"problem_data": {"parallel_type": "OpenMP"}}, 2, None, "requires problem_data.parallel_type"),
    ({"problem_data": {}}, 2, None, "requires problem_data.parallel_type"),
    ({"problem_data": {"parallel_type": "MPI"}}, None, None, "pass mpi_ranks"),
    ({"problem_data": {"parallel_type": "MPI"}, "orchestrator": {}, "stages": {}}, 2, None,
     "multi-stage"),
    ({"problem_data": {"parallel_type": "MPI"}}, 0, None, "positive integer"),
    ({"problem_data": {"parallel_type": "MPI"}}, True, None, "positive integer"),
    ({"problem_data": {"parallel_type": "OpenMP"}}, None, 0, "positive integer"),
])
def test_validate_request_rejects(params, ranks, threads, message):
    with pytest.raises(ValueError, match=message):
        mpi_launch.validate_request(params, ranks, threads)


def test_validate_request_accepts_consistent_requests():
    mpi_launch.validate_request({"problem_data": {"parallel_type": "MPI"}}, 2, 4)
    mpi_launch.validate_request({"problem_data": {"parallel_type": "OpenMP"}}, None, 4)
    mpi_launch.validate_request({}, None, None)
    # Not plain JSON (e.g. `//` comments): cross-checks cannot be evaluated.
    mpi_launch.validate_request(None, 2, None)


def test_rank_from_environ():
    assert mpi_launch.rank_from_environ({}) is None
    assert mpi_launch.rank_from_environ({"OMPI_COMM_WORLD_RANK": "3"}) == 3
    assert mpi_launch.rank_from_environ({"PMI_RANK": "1"}) == 1
    assert mpi_launch.rank_from_environ({"PMI_RANK": "junk"}) is None


def test_check_capability_requires_mpi_enabled_build(fake_launcher, monkeypatch):
    monkeypatch.setattr(mpi_launch, "probe_kratos_mpi",
                        lambda: {"mpi_module": False, "mpi_error": "ImportError: no mpi"})
    with pytest.raises(RuntimeError, match="no MPI support.*no mpi"):
        mpi_launch.check_capability(2)


# ------------------------------------------------------------ rank logging

def _run_redirect(tmp_path, rank_env: dict[str, str]):
    log_dir = tmp_path / "ranks"
    code = ("from kratos_mcp import runner; import sys;"
            f"runner.redirect_rank_output({str(log_dir)!r});"
            "print('hello'); print('oops', file=sys.stderr)")
    env = {**os.environ, **rank_env,
           "PYTHONPATH": str(Path(mpi_launch.__file__).resolve().parent.parent)}
    for name in mpi_launch._RANK_VARS:
        if name not in rank_env:
            env.pop(name, None)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    return out, log_dir


def test_rank_gt_zero_output_goes_to_rank_log(tmp_path):
    out, log_dir = _run_redirect(tmp_path, {"OMPI_COMM_WORLD_RANK": "1"})
    assert out.returncode == 0
    assert out.stdout == "" and out.stderr == ""
    # stdout is block-buffered and stderr is not once both point at the file,
    # so the relative order of the two lines is not defined.
    assert sorted((log_dir / "rank-1.log").read_text().split()) == ["hello", "oops"]


def test_rank_zero_and_serial_keep_stdout(tmp_path):
    out, log_dir = _run_redirect(tmp_path, {"OMPI_COMM_WORLD_RANK": "0"})
    assert out.stdout.strip() == "hello" and not log_dir.exists()
    out, log_dir = _run_redirect(tmp_path, {})
    assert out.stdout.strip() == "hello" and not log_dir.exists()


# ------------------------------------------------------------ job lifecycle

def test_missing_launcher_fails_before_job_is_created(tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(RuntimeError, match="No MPI launcher"):
        jobs.start(str(_case(tmp_path)), mpi_ranks=2)
    assert list(jobs.jobs_root().iterdir()) == []


def test_missing_kratos_mpi_support_fails_before_job_is_created(
        tmp_path, fake_env, fake_launcher, monkeypatch):
    monkeypatch.setattr(mpi_launch, "probe_kratos_mpi", lambda: {"mpi_module": False})
    with pytest.raises(RuntimeError, match="no MPI support"):
        jobs.start(str(_case(tmp_path)), mpi_ranks=2)
    assert list(jobs.jobs_root().iterdir()) == []


def test_serial_case_with_ranks_is_rejected_without_a_job(tmp_path, fake_env, fake_launcher):
    with pytest.raises(ValueError, match="requires problem_data.parallel_type"):
        jobs.start(str(_case(tmp_path, "OpenMP")), mpi_ranks=2)
    assert list(jobs.jobs_root().iterdir()) == []


def test_mpi_job_records_launch_and_cancel_kills_every_rank(tmp_path, fake_env, fake_launcher):
    meta = jobs.start(str(_case(tmp_path)), mpi_ranks=2, omp_threads=3)
    try:
        manifest = json.loads((jobs.jobs_root() / meta.job_id / "manifest.json").read_text())
        assert manifest["mpi"]["ranks"] == 2 and manifest["omp_threads"] == 3
        assert manifest["command"][0].endswith("fake_mpiexec")
        assert manifest["command"][1:3] == ["-n", "2"]
        assert "--rank-log-dir" in manifest["command"]
        assert meta.extra["mpi"]["ranks"] == 2

        # launcher + two ranks (which ignore SIGTERM) are in the job's group
        assert _wait_for(lambda: len(mpi_launch.group_members(meta.pid)) >= 3)
        launched = json.loads(fake_launcher.read_text())
        assert launched["omp"] == launched["mkl"] == "3"
    finally:
        cancelled = jobs.cancel(meta.job_id, grace_seconds=0.5)
    assert cancelled["state"] == "cancelled"
    assert mpi_launch.group_members(meta.pid) == []


def test_status_lists_rank_logs_and_logs_select_rank(tmp_path, fake_env, fake_launcher):
    meta = jobs.start(str(_case(tmp_path)), mpi_ranks=2)
    try:
        job_dir = jobs.jobs_root() / meta.job_id
        (job_dir / "stdout.log").write_text("main\n")
        (job_dir / "ranks" / "rank-1.log").write_text("one\nrank one detail\n")
        assert jobs.status(meta.job_id)["rank_logs"] == ["rank-1.log"]
        assert jobs.logs(meta.job_id) == "main"
        assert jobs.logs(meta.job_id, rank=0) == "main"
        assert jobs.logs(meta.job_id, rank=1, grep="detail") == "rank one detail"
        with pytest.raises(ValueError, match="no log for rank 5"):
            jobs.logs(meta.job_id, rank=5)
    finally:
        jobs.cancel(meta.job_id, grace_seconds=0.5)


def test_stray_ranks_are_killed_when_launcher_exits_first(
        tmp_path, fake_env, fake_launcher, monkeypatch):
    monkeypatch.setenv("FAKE_MPI_DETACH", "1")
    meta = jobs.start(str(_case(tmp_path)), mpi_ranks=2)
    assert _wait_for(lambda: jobs.status(meta.job_id)["state"] in jobs.TERMINAL_STATES)
    assert _wait_for(lambda: mpi_launch.group_members(meta.pid) == [])
    assert jobs.status(meta.job_id)["extra"].get("stray_ranks_killed") is True


def test_rerun_preserves_mpi_settings(tmp_path, fake_env, fake_launcher):
    first = jobs.start(str(_case(tmp_path)), isolate=True, mpi_ranks=2, omp_threads=2)
    jobs.cancel(first.job_id, grace_seconds=0.5)
    second = jobs.rerun(first.job_id)
    try:
        assert second.extra["mpi"]["ranks"] == 2
        assert second.extra["omp_threads"] == 2
        assert second.extra["rerun_of"] == first.job_id
    finally:
        jobs.cancel(second.job_id, grace_seconds=0.5)


def test_supervised_jobs_reject_mpi(tmp_path, fake_env, fake_launcher):
    with pytest.raises(ValueError, match="supervised"):
        jobs.start(str(_case(tmp_path)), isolate=True, mpi_ranks=2, _defer_launch=True)
