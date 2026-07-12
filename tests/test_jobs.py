from __future__ import annotations

import json
import time

import pytest

from kratos_mcp import jobs


@pytest.fixture(autouse=True)
def isolated_jobs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    yield


def _fake_finished_job(state_dir_meta: dict) -> str:
    """Write a job dir on disk as if a previous server had created it."""
    job_id = "20260101-000000-abcdef"
    job_dir = jobs.jobs_root() / job_id
    job_dir.mkdir(parents=True)
    meta = {
        "job_id": job_id, "case_dir": "/tmp/x", "parameters_file": "ProjectParameters.json",
        "state": "running", "pid": 99999999, "returncode": None,
        "created_at": time.time(), "started_at": time.time(), "finished_at": None,
        "analysis_type": None, "extra": {},
        **state_dir_meta,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta))
    return job_id


def test_unknown_job_raises():
    with pytest.raises(KeyError):
        jobs.status("nope")


def test_orphaned_job_success_detected_from_log():
    job_id = _fake_finished_job({})
    (jobs.jobs_root() / job_id / "stdout.log").write_text(
        "::[KSM Simulation]:: : Analysis -END-\n")
    status = jobs.status(job_id)
    assert status["state"] == "succeeded"
    assert status["returncode"] == 0


def test_orphaned_job_failure_detected():
    job_id = _fake_finished_job({})
    (jobs.jobs_root() / job_id / "stdout.log").write_text("RuntimeError: boom\n")
    assert jobs.status(job_id)["state"] == "failed"


def test_list_jobs_filters_by_state():
    _fake_finished_job({})
    all_jobs = jobs.list_jobs()
    assert len(all_jobs) == 1
    assert jobs.list_jobs(state="cancelled") == []


def test_logs_tail_and_grep():
    job_id = _fake_finished_job({"state": "succeeded", "returncode": 0})
    (jobs.jobs_root() / job_id / "stdout.log").write_text(
        "\n".join(f"line {i}" for i in range(200)) + "\nERROR: bad thing\n")
    assert len(jobs.logs(job_id, tail=10).splitlines()) == 10
    assert jobs.logs(job_id, grep="error") == "ERROR: bad thing"


def test_cancel_terminal_job_is_noop():
    job_id = _fake_finished_job({"state": "succeeded", "returncode": 0})
    assert jobs.cancel(job_id)["state"] == "succeeded"


def test_start_spawns_real_process(tmp_path):
    """Job start/refresh state machine with a real (non-Kratos) subprocess:
    point the runner at a case dir; it fails fast, and the manager records it."""
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text("{}")
    try:
        meta = jobs.start(str(case))
    except RuntimeError:
        pytest.skip("Kratos not available")
    assert meta.state == "running"
    deadline = time.time() + 60
    while time.time() < deadline:
        status = jobs.status(meta.job_id)
        if status["state"] in jobs.TERMINAL_STATES:
            break
        time.sleep(0.5)
    # Empty parameters cannot run an analysis: the runner must fail cleanly.
    assert status["state"] == "failed"
    assert status["returncode"] not in (None, 0)
