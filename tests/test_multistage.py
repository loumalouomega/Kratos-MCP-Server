from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kratos_mcp import mdpa
from kratos_mcp.tools import scaffold

EXAMPLES = Path(__file__).resolve().parent.parent / "src" / "kratos_mcp" / "examples"


def _tools():
    """Capture scaffold's MCP-registered tool callables."""
    captured: dict = {}

    class FakeMCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    scaffold.register(FakeMCP())
    return captured


# --- Unit: composition shape (no Kratos) ------------------------------------

def test_create_multistage_project_shape(tmp_path):
    tools = _tools()
    res = tools["create_multistage_project"](
        directory=str(tmp_path),
        stages=[
            {"name": "step1", "template": "structural_static", "overrides": {"end_time": 1.0}},
            {"name": "step2", "template": "structural_static", "overrides": {"end_time": 2.0}},
        ],
        name="ms",
    )
    assert res["execution_list"] == ["step1", "step2"]

    pp = json.loads((tmp_path / "ProjectParameters.json").read_text())
    assert pp["orchestrator"]["name"] == "Orchestrators.KratosMultiphysics.SequentialOrchestrator"
    assert pp["orchestrator"]["settings"]["execution_list"] == ["step1", "step2"]
    assert set(pp["stages"]) == {"step1", "step2"}

    # First stage imports the mesh; the second reuses the shared model part.
    s1 = pp["stages"]["step1"]["stage_settings"]["solver_settings"]
    s2 = pp["stages"]["step2"]["stage_settings"]["solver_settings"]
    assert s1["model_import_settings"]["input_type"] == "mdpa"
    assert s2["model_import_settings"]["input_type"] == "use_input_model_part"


def test_create_multistage_rejects_unknown_template(tmp_path):
    tools = _tools()
    res = tools["create_multistage_project"](
        directory=str(tmp_path),
        stages=[{"name": "s", "template": "does_not_exist"}])
    assert "error" in res


def test_validate_multistage_case(tmp_path):
    tools = _tools()
    tools["create_multistage_project"](
        directory=str(tmp_path),
        stages=[{"name": "a", "template": "structural_static"},
                {"name": "b", "template": "structural_static"}],
        name="ms")
    mdpa.create_rectangle_mesh(1.0, 0.2, 4, 2).write(tmp_path / "mesh.mdpa")
    result = scaffold.validate_case_files(tmp_path, deep=False)
    assert result["valid"], result["issues"]


# --- Integration: run a multi-stage case with real Kratos -------------------

@pytest.mark.kratos
def test_multistage_runs_all_stages(tmp_path, monkeypatch):
    from kratos_mcp import jobs

    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    case = tmp_path / "ms"
    tools = _tools()
    tools["create_multistage_project"](
        directory=str(case),
        stages=[{"name": "load_1", "template": "structural_static", "overrides": {"end_time": 1.0}},
                {"name": "load_2", "template": "structural_static", "overrides": {"end_time": 2.0}}],
        name="cantilever_ms")
    # Reuse the real cantilever mesh (matches the Structure.* model parts).
    (case / "mesh.mdpa").write_text((EXAMPLES / "cantilever" / "mesh.mdpa").read_text())

    assert scaffold.validate_case_files(case, deep=False)["valid"]

    meta = jobs.start(str(case))
    deadline = time.time() + 240
    while time.time() < deadline:
        status = jobs.status(meta.job_id)
        if status["state"] in jobs.TERMINAL_STATES:
            break
        time.sleep(1.0)
    else:
        jobs.cancel(meta.job_id)
        pytest.fail("multistage job did not finish in time")

    log = jobs.logs(meta.job_id, tail=200)
    assert status["state"] == "succeeded", log
    # Both stages must have completed an analysis.
    assert log.count("Analysis -END-") >= 2, log
