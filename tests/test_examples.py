"""Tests for the static example files served by kratos://examples/*.

The cantilever example (src/kratos_mcp/examples/cantilever/) is copied
verbatim by tools/resources.py rather than rendered at request time -- see
CLAUDE.md. These tests keep that copy honest: structurally on every run,
and physically (against a real Kratos build) in the kratos-marked test.
"""

from __future__ import annotations

import json
import time

import pytest

from kratos_mcp import jobs, mdpa
from kratos_mcp.tools.resources import EXAMPLES_DIR
from kratos_mcp.tools.scaffold import validate_case_files

CANTILEVER_DIR = EXAMPLES_DIR / "cantilever"


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
