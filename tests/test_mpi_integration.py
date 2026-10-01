"""Two-rank vs serial acceptance test for MPI launch (roadmap probe).

Needs an MPI launcher plus a Kratos build with KratosMultiphysics.mpi,
TrilinosApplication, StructuralMechanicsApplication and
LinearSolversApplication; it skips itself otherwise (CI has no Trilinos).

UNVERIFIED against a real distributed build: this repository's author
environment had no MPI runtime, so the case setup (MPI import of a plain
.mdpa, `point_output_process` file naming) is the first thing to check when
this runs for the first time.
"""
import json
import time
from pathlib import Path

import pytest

from kratos_mcp import bridge, jobs, mdpa, mpi_launch
from kratos_mcp.tools import scaffold

pytestmark = pytest.mark.kratos

PROBE = [1.0, 0.1, 0.0]


def _wait(meta, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = jobs.status(meta.job_id)
        if status["state"] in jobs.TERMINAL_STATES:
            assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=60)
            return status
        time.sleep(0.2)
    jobs.cancel(meta.job_id)
    pytest.fail("Job timed out")


def _case(tmp_path: Path, name: str, *, distributed: bool) -> Path:
    case = tmp_path / name
    case.mkdir()
    values = scaffold._resolve_values("structural_static", {})
    for filename in ("ProjectParameters.json", "Materials.json"):
        (case / filename).write_text(
            scaffold.render_template_file("structural_static", filename, values))
    mdpa.create_rectangle_mesh(1.0, 0.2, 8, 2).write(case / "mesh.mdpa")
    params = json.loads((case / "ProjectParameters.json").read_text())
    params["processes"]["loads_process_list"].append(scaffold._direction_to_conditions_process(
        "Structure.right", "LINE_LOAD", 1e4, [0.0, -1.0, 0.0], [0.0, "End"]))
    # Same observable in both runs: nodal displacement at a probe point.
    params["processes"]["list_other_processes"].append({
        "python_module": "point_output_process",
        "kratos_module": "KratosMultiphysics",
        "process_name": "PointOutputProcess",
        "Parameters": {
            "model_part_name": "Structure",
            "entity_type": "node",
            "positions": [PROBE],
            "output_variables": ["DISPLACEMENT"],
            "output_file_settings": {"file_name": "probe", "output_path": "probe",
                                     "write_buffer_size": 1},
        },
    })
    if distributed:
        presets = json.loads((Path(mpi_launch.__file__).parent / "templates"
                              / "linear_solvers.json").read_text())
        params["problem_data"]["parallel_type"] = "MPI"
        params["solver_settings"]["linear_solver_settings"] = presets["amesos"]["settings"]
    (case / "ProjectParameters.json").write_text(json.dumps(params, indent=2))
    return case


def _probe_displacement(job_status: dict) -> list[float]:
    """Last probe sample (only the rank owning the node writes a file)."""
    files = sorted(Path(job_status["case_dir"]).glob("probe/*"))
    assert files, f"no probe output under {job_status['case_dir']}"
    rows = [line.split() for line in files[0].read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    return [float(v) for v in rows[-1][1:4]]


def test_two_rank_run_matches_serial_and_cancel_leaves_no_ranks(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    if mpi_launch.find_launcher() is None:
        pytest.skip("no MPI launcher (mpiexec/mpirun) on PATH")
    support = bridge.run_op("mpi_support", use_cache=False)
    if not (support.get("mpi_module") and support.get("trilinos")):
        pytest.skip(f"Kratos build lacks MPI/Trilinos support: {support}")
    apps = bridge.run_op("list_applications", use_cache=False)
    required = {"StructuralMechanicsApplication", "LinearSolversApplication"}
    if not required.issubset(apps):
        pytest.skip(f"Missing optional applications: {sorted(required - set(apps))}")

    serial = jobs.start(str(_case(tmp_path, "serial", distributed=False)), isolate=True)
    serial_status = _wait(serial)
    reference = _probe_displacement(serial_status)
    assert abs(reference[1]) > 0  # the probe actually moved

    parallel = jobs.start(str(_case(tmp_path, "mpi", distributed=True)),
                          isolate=True, mpi_ranks=2)
    parallel_status = _wait(parallel)
    assert parallel_status["extra"]["mpi"]["ranks"] == 2
    assert sorted(parallel_status["rank_logs"]) == ["rank-1.log"]
    result = _probe_displacement(parallel_status)
    assert result == pytest.approx(reference, rel=1e-6, abs=1e-12)

    # Cancelling a distributed job must not leave worker ranks behind.
    victim = jobs.start(str(_case(tmp_path, "cancel", distributed=True)),
                        isolate=True, mpi_ranks=2)
    cancelled = jobs.cancel(victim.job_id)
    assert cancelled["state"] in ("cancelled", "succeeded", "failed")
    assert mpi_launch.group_members(victim.pid or 0) == []
