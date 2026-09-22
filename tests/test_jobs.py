from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from kratos_mcp import jobs, kratos_env


@pytest.fixture(autouse=True)
def isolated_jobs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    yield


@pytest.fixture
def snapshot_environment(monkeypatch):
    """Let snapshot tests run in the unit-test job without a Kratos build."""
    env = kratos_env.KratosEnv(
        root=None, pythonpath=None, libs=None, source=None,
        pip_installed=True, python=sys.executable)
    monkeypatch.setattr(jobs.kratos_env, "resolve", lambda: env)
    return env


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


def test_isolated_start_writes_manifest_and_snapshot(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text('{"problem_data": {}, "solver_settings": {}}')
    (case / "Materials.json").write_text('{"properties": []}')
    meta = jobs.start(str(case), isolate=True)
    job_dir = jobs.jobs_root() / meta.job_id
    manifest = json.loads((job_dir / "manifest.json").read_text())
    assert manifest["version"] == 1
    assert manifest["isolate"] is True
    assert manifest["analysis_overrides"] == {"analysis_type": None, "analysis_class": None}
    assert manifest["input_hashes"]["ProjectParameters.json"]
    assert (job_dir / "snapshot" / "Materials.json").is_file()
    assert Path(meta.case_dir) == job_dir / "execution"
    assert meta.extra["snapshot_dir"] == str(job_dir / "snapshot")


def test_isolated_start_requires_mapping_for_external_reference(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text(
        '{"solver_settings": {"model_import_settings": {"input_filename": "/tmp/outside-mesh"}}}')
    with pytest.raises(RuntimeError, match="external input_filename"):
        jobs.start(str(case), isolate=True)


def test_isolated_start_copies_and_rewrites_external_mesh(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    external = tmp_path / "mesh.mdpa"
    external.write_text("Begin ModelPartData\nEnd ModelPartData\n")
    (case / "ProjectParameters.json").write_text(
        json.dumps({"solver_settings": {"model_import_settings": {
            "input_filename": str(external.with_suffix(""))}}}))
    meta = jobs.start(str(case), isolate=True, external_inputs={str(external): "inputs/mesh.mdpa"})
    job_dir = jobs.jobs_root() / meta.job_id
    snapshot_params = json.loads((job_dir / "snapshot" / "ProjectParameters.json").read_text())
    assert snapshot_params["solver_settings"]["model_import_settings"]["input_filename"] == "inputs/mesh"
    assert (job_dir / "snapshot" / "inputs" / "mesh.mdpa").read_text() == external.read_text()


def test_isolated_start_rejects_escaping_output_path(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text('{"output_processes": {"vtk_output": [{"Parameters": {"output_path": "../outside"}}]}}')
    with pytest.raises(RuntimeError, match="output path"):
        jobs.start(str(case), isolate=True)


def test_isolated_start_rejects_directory_symlink(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (case / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="directory symlink"):
        jobs.start(str(case), isolate=True)


def test_isolated_snapshot_can_be_rerun_and_detects_tampering(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "ProjectParameters.json").write_text("{}")
    meta = jobs.start(str(case), isolate=True)
    snapshot = Path(meta.extra["snapshot_dir"])
    (snapshot / "ProjectParameters.json").write_text('{"changed": true}')
    with pytest.raises(RuntimeError, match="Snapshot input changed"):
        jobs.rerun(meta.job_id)


def test_rerun_reads_preserved_snapshot_after_original_changes(tmp_path, snapshot_environment):
    case = tmp_path / "case"
    case.mkdir()
    (case / "Materials.json").write_text('{"value": "original"}')
    (case / "ProjectParameters.json").write_text(json.dumps({
        "solver_settings": {"material_import_settings": {"materials_filename": "Materials.json"}}
    }))
    meta = jobs.start(str(case), isolate=True)
    (case / "Materials.json").write_text('{"value": "changed"}')
    rerun = jobs.rerun(meta.job_id)
    rerun_snapshot = Path(rerun.extra["snapshot_dir"])
    assert json.loads((rerun_snapshot / "Materials.json").read_text())["value"] == "original"
