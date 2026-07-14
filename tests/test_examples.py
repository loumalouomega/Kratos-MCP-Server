"""Tests for the static example files served by kratos://examples/*.

The cantilever example (src/kratos_mcp/examples/cantilever/) and the
naca_airfoil example (src/kratos_mcp/examples/naca_airfoil/) are copied
verbatim by tools/resources.py rather than rendered at request time -- see
CLAUDE.md. These tests keep those copies honest: structurally on every run,
and physically (against a real Kratos build) in the kratos-marked tests.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from kratos_mcp import jobs, mdpa
from kratos_mcp.tools.resources import EXAMPLES_DIR
from kratos_mcp.tools.scaffold import validate_case_files

CANTILEVER_DIR = EXAMPLES_DIR / "cantilever"
NACA_AIRFOIL_DIR = EXAMPLES_DIR / "naca_airfoil"


def test_cantilever_files_exist():
    for name in ("mesh.mdpa", "ProjectParameters.json", "Materials.json"):
        assert (CANTILEVER_DIR / name).is_file(), name


def test_cantilever_mesh_parses_and_validates():
    m = mdpa.read(CANTILEVER_DIR / "mesh.mdpa")
    assert m.validate() == []
    info = m.inspect()
    assert info["num_nodes"] == 10
    assert info["num_elements"] == 4
    assert set(info["sub_model_parts"]) == {"domain", "left", "right", "bottom", "top"}


def test_cantilever_json_files_are_valid():
    pp = json.loads((CANTILEVER_DIR / "ProjectParameters.json").read_text())
    assert pp["solver_settings"]["solver_type"] == "Static"
    mats = json.loads((CANTILEVER_DIR / "Materials.json").read_text())
    assert mats["properties"][0]["Material"]["Variables"]["YOUNG_MODULUS"] == 210000000000.0


def test_cantilever_case_is_valid():
    result = validate_case_files(CANTILEVER_DIR, deep=False)
    assert result["valid"], result["issues"]


@pytest.mark.kratos
def test_cantilever_example_runs_and_matches_documented_result(tmp_path, monkeypatch):
    """Copies the example verbatim (as the resource does) and runs it for
    real, asserting the exact displacement documented in resources.py and
    CLAUDE.md. If this ever fails, the on-disk example is stale."""
    import shutil

    import meshio
    import numpy as np

    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    case = tmp_path / "cantilever"
    shutil.copytree(CANTILEVER_DIR, case)

    meta = jobs.start(str(case))
    deadline = time.time() + 120.0
    status = jobs.status(meta.job_id)
    while time.time() < deadline and status["state"] not in jobs.TERMINAL_STATES:
        time.sleep(1.0)
        status = jobs.status(meta.job_id)
    assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)

    vtk_files = sorted((case / "vtk_output").glob("*.vtk"))
    assert vtk_files
    mesh = meshio.read(vtk_files[-1])
    disp = np.asarray(mesh.point_data["DISPLACEMENT"])
    tip = disp[np.argmax(np.asarray(mesh.points)[:, 0])]
    assert np.allclose(tip, [-3.6684305e-05, -2.5312169e-04, 0.0], atol=1e-8)


def test_naca_airfoil_files_exist():
    for name in ("mesh.mdpa", "ProjectParameters.json", "Materials.json"):
        assert (NACA_AIRFOIL_DIR / name).is_file(), name


def test_naca_airfoil_mesh_parses_and_validates():
    m = mdpa.read(NACA_AIRFOIL_DIR / "mesh.mdpa")
    assert m.validate() == []
    info = m.inspect()
    assert info["num_nodes"] == 21191
    assert info["num_elements"] == 41147
    assert info["num_conditions"] == 1233
    assert set(info["sub_model_parts"]) == {
        "FluidParts_Fluid", "AutomaticInlet2D_Left", "Outlet2D_Right",
        "NoSlip2D_Top", "NoSlip2D_Bottom", "NoSlip2D_Aerofoil",
    }


def test_naca_airfoil_json_files_are_valid():
    pp = json.loads((NACA_AIRFOIL_DIR / "ProjectParameters.json").read_text())
    assert pp["solver_settings"]["solver_type"] == "Monolithic"
    assert pp["solver_settings"]["compute_reactions"] is True
    mats = json.loads((NACA_AIRFOIL_DIR / "Materials.json").read_text())
    assert mats["properties"][0]["Material"]["Variables"]["DYNAMIC_VISCOSITY"] == 0.001


def test_naca_airfoil_case_is_valid():
    result = validate_case_files(NACA_AIRFOIL_DIR, deep=False)
    assert result["valid"], result["issues"]


@pytest.mark.kratos
def test_naca_airfoil_example_runs(tmp_path, monkeypatch):
    """Copies the example verbatim but shortens the run to a handful of
    steps -- the shipped example's full 40-step demo takes ~4 minutes (see
    docs/tutorials/naca-airfoil.md and notebooks/naca_airfoil.ipynb for that
    richer run); this just proves the pipeline solves and produces a sane
    fluid field, matching the early-transient values documented there."""
    import shutil

    import meshio
    import numpy as np

    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    case = tmp_path / "naca_airfoil"
    shutil.copytree(NACA_AIRFOIL_DIR, case)

    pp_path = case / "ProjectParameters.json"
    params = json.loads(pp_path.read_text())
    params["problem_data"]["end_time"] = 0.2  # 4 steps, enough to prove the pipeline runs
    pp_path.write_text(json.dumps(params))

    meta = jobs.start(str(case))
    deadline = time.time() + 120.0
    status = jobs.status(meta.job_id)
    while time.time() < deadline and status["state"] not in jobs.TERMINAL_STATES:
        time.sleep(1.0)
        status = jobs.status(meta.job_id)
    assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)

    vtk_files = sorted((case / "vtk_output").glob("*.vtk"),
                        key=lambda p: int(re.findall(r"\d+", p.stem)[-1]))
    assert vtk_files
    mesh = meshio.read(vtk_files[-1])
    velocity = np.asarray(mesh.point_data["VELOCITY"])
    pressure = np.asarray(mesh.point_data["PRESSURE"])
    assert np.all(np.isfinite(velocity)) and np.all(np.isfinite(pressure))
    # This early into the impulsive start the field is still transient (the
    # shipped example's own pressure range only settles to ~[-0.34, 0.57] by
    # step 8+ -- see naca-airfoil.md) -- just check a non-degenerate solve
    # happened: a non-trivial pressure field, and flow reaching close to
    # freestream speed somewhere away from the no-slip walls.
    assert pressure.max() - pressure.min() > 1.0
    assert np.linalg.norm(velocity, axis=1).max() > 0.5
