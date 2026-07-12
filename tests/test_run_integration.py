"""End-to-end integration tests: scaffold a case with our own tools, run it
with the real Kratos build, and verify the physics in the VTK output."""

from __future__ import annotations

import json
import time

import pytest

from kratos_mcp import jobs, mdpa
from kratos_mcp.tools import scaffold

pytestmark = pytest.mark.kratos


@pytest.fixture(autouse=True)
def isolated_jobs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    yield


def _run_to_completion(case_dir, timeout=240.0) -> dict:
    meta = jobs.start(str(case_dir))
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = jobs.status(meta.job_id)
        if status["state"] in jobs.TERMINAL_STATES:
            return status
        time.sleep(1.0)
    jobs.cancel(meta.job_id)
    pytest.fail(f"job did not finish within {timeout}s")


def _scaffold(case, template, overrides=None, mesh=None):
    case.mkdir(parents=True, exist_ok=True)
    values = scaffold._resolve_values(template, overrides)
    (case / "ProjectParameters.json").write_text(
        scaffold.render_template_file(template, "ProjectParameters.json", values))
    (case / "Materials.json").write_text(
        scaffold.render_template_file(template, "Materials.json", values))
    if mesh is not None:
        mesh.write(case / "mesh.mdpa")
    return values


def test_structural_cantilever(tmp_path):
    """Cantilever plate, fixed left edge, line load on the right edge.
    Tip deflection must be within 20% of Euler-Bernoulli beam theory."""
    import meshio
    import numpy as np

    case = tmp_path / "cantilever"
    _scaffold(case, "structural_static",
              mesh=mdpa.create_rectangle_mesh(1.0, 0.2, 20, 4))
    pp = json.loads((case / "ProjectParameters.json").read_text())
    pp["processes"]["loads_process_list"].append(
        scaffold._direction_to_conditions_process(
            "Structure.right", "LINE_LOAD", 1.0e6, [0.0, -1.0, 0.0], [0.0, "End"]))
    (case / "ProjectParameters.json").write_text(json.dumps(pp))

    result = scaffold.validate_case_files(case)
    assert result["valid"], result["issues"]

    status = _run_to_completion(case)
    assert status["state"] == "succeeded", jobs.logs(status["job_id"], tail=40)

    vtk_files = sorted((case / "vtk_output").glob("*.vtk"))
    assert vtk_files, "no VTK output written"
    mesh = meshio.read(vtk_files[-1])
    disp = np.asarray(mesh.point_data["DISPLACEMENT"])
    tip = disp[np.argmax(np.asarray(mesh.points)[:, 0])]

    # P = q*h = 1e6 * 0.2 = 2e5 N; delta = P L^3 / (3 E I), I = h^3/12
    expected = 2.0e5 * 1.0**3 / (3 * 2.1e11 * (0.2**3 / 12))
    assert tip[1] < 0, "tip must deflect downward"
    assert abs(abs(tip[1]) - expected) / expected < 0.2, (tip, expected)


def test_thermal_bar_linear_profile(tmp_path):
    """Steady conduction in a bar with T=100 (left) and T=0 (right):
    the temperature field must be linear in x."""
    import meshio
    import numpy as np

    case = tmp_path / "bar"
    _scaffold(case, "thermal_stationary",
              mesh=mdpa.create_rectangle_mesh(
                  1.0, 0.1, 20, 2, element_name="Element2D3N",
                  condition_name="ThermalFace2D2N", triangles=True))
    pp = json.loads((case / "ProjectParameters.json").read_text())
    pp["processes"]["constraints_process_list"].append(
        scaffold._scalar_process("ThermalModelPart.right", "TEMPERATURE",
                                 0.0, True, [0.0, "End"]))
    (case / "ProjectParameters.json").write_text(json.dumps(pp))

    status = _run_to_completion(case)
    assert status["state"] == "succeeded", jobs.logs(status["job_id"], tail=40)

    vtk_files = sorted((case / "vtk_output").glob("*.vtk"))
    mesh = meshio.read(vtk_files[-1])
    T = np.asarray(mesh.point_data["TEMPERATURE"])
    x = np.asarray(mesh.points)[:, 0]
    expected = 100.0 * (1.0 - x)
    assert np.allclose(T, expected, atol=0.5), np.abs(T - expected).max()


def test_job_cancel(tmp_path):
    """A long transient run can be cancelled."""
    case = tmp_path / "long"
    _scaffold(case, "thermal_transient",
              overrides={"end_time": 10000.0, "time_step": 0.001},
              mesh=mdpa.create_rectangle_mesh(
                  1.0, 0.1, 30, 3, element_name="Element2D3N",
                  condition_name="ThermalFace2D2N", triangles=True))
    meta = jobs.start(str(case))
    time.sleep(5)  # let it get past initialization
    result = jobs.cancel(meta.job_id)
    assert result["state"] == "cancelled"
    time.sleep(1)
    assert jobs.status(meta.job_id)["state"] == "cancelled"
