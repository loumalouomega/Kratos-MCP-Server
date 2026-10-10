"""Interactive sessions against a real Kratos build: step the load-stepping
cantilever and verify the physics, the batch equivalence and live edits."""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from kratos_mcp import jobs, kratos_env, sessions
from kratos_mcp.tools.resources import EXAMPLES_DIR

pytestmark = pytest.mark.kratos

CASE = EXAMPLES_DIR / "load_stepping_cantilever"
TIP_NODE = 105


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("KRATOS_MCP_ALLOW_EXEC", "1")
    yield
    sessions.close_all()


@pytest.fixture(scope="module", autouse=True)
def require_structural():
    env = kratos_env.resolve()
    probe = subprocess.run(
        [env.python, "-c", "import KratosMultiphysics.StructuralMechanicsApplication, "
                           "KratosMultiphysics.LinearSolversApplication"],
        env=env.build_env(), capture_output=True, timeout=120)
    if probe.returncode != 0:
        pytest.skip("StructuralMechanicsApplication/LinearSolversApplication unavailable")


@pytest.fixture
def case(tmp_path):
    dest = tmp_path / "case"
    shutil.copytree(CASE, dest)
    return dest


def _tip_uy(sid) -> float:
    return sessions.request(sid, "inspect_node",
                            {"node_id": TIP_NODE, "variables": ["DISPLACEMENT"]}
                            )["values"]["DISPLACEMENT"][1]


def test_stepping_is_linear_in_time_and_matches_batch(case, tmp_path):
    info = sessions.start(str(case), timeout=120)
    sid = info["session_id"]
    assert info["time"] == 0.0 and info["end_time"] == 1.0 and info["nodes"] == 105

    sessions.request(sid, "advance", {"steps": 1})
    u1 = _tip_uy(sid)
    adv = sessions.request(sid, "advance", {"steps": 1})
    u2 = _tip_uy(sid)
    assert adv["time"] == 0.25 and adv["history"][0]["converged"] is True
    assert u1 < 0 and u2 == pytest.approx(2 * u1, rel=1e-6)

    rest = sessions.request(sid, "advance", {"steps": 100})   # clipped at end_time
    assert rest["advanced"] == 6 and rest["finished"] is True
    assert rest["stop_reason"] == "end_time_reached"
    stepped = _tip_uy(sid)
    assert stepped == pytest.approx(8 * u1, rel=1e-4)   # linear problem, tiny geometric effects
    summary = sessions.request(sid, "field_summary", {"variable": "DISPLACEMENT"})
    assert summary["argmax"]["coordinates"][0] == pytest.approx(5.0, abs=1e-3)   # max |u| is at the free end
    stress = sessions.request(sid, "field_summary",
                              {"variable": "VON_MISES_STRESS", "location": "elements"})
    assert stress["max"] > 1e7
    assert sessions.close(sid)["finalized"] is True

    # the batch job over the very same files gives the same tip deflection
    batch_case = tmp_path / "batch"
    shutil.copytree(CASE, batch_case)
    meta = jobs.start(str(batch_case))
    deadline = time.time() + 120
    while time.time() < deadline and jobs.status(meta.job_id)["state"] not in jobs.TERMINAL_STATES:
        time.sleep(0.5)
    assert jobs.status(meta.job_id)["state"] == "succeeded"
    import meshio
    import numpy as np
    mesh = meshio.read(sorted((batch_case / "vtk_output").glob("*.vtk"))[-1])
    batch_tip = np.asarray(mesh.point_data["DISPLACEMENT"])[TIP_NODE - 1][1]
    assert stepped == pytest.approx(batch_tip, rel=1e-6)   # same files, same solver path


def test_until_time_and_model_info(case):
    sid = sessions.start(str(case), timeout=120)["session_id"]
    adv = sessions.request(sid, "advance", {"until_time": 0.5})
    assert adv["advanced"] == 4 and adv["stop_reason"] == "until_time_reached"
    info = sessions.request(sid, "model_info", {})
    assert info["process_info"]["STEP"] == 4 and info["process_info"]["TIME"] == 0.5
    assert {sub["name"] for sub in info["sub_model_parts"]} >= {"left", "right", "domain"}
    elem = sessions.request(sid, "inspect_element",
                            {"element_id": 1, "variables": ["VON_MISES_STRESS"]})
    assert len(elem["node_ids"]) == 4 and len(elem["values"]["VON_MISES_STRESS"]) >= 1
    params = sessions.request(sid, "get_parameters", {"path": "/solver_settings/time_stepping"})
    assert params["time_step"] == 0.125
    sessions.close(sid)


def test_fixing_a_node_changes_the_response(case):
    sid = sessions.start(str(case), timeout=120)["session_id"]
    sessions.request(sid, "advance", {"steps": 1})
    free = _tip_uy(sid)
    # pin the tip vertically for the remaining steps: it can no longer follow the ramp
    sessions.request(sid, "set_nodal_values", {"variable": "DISPLACEMENT",
                                               "value": [0.0, free, 0.0],
                                               "node_ids": [TIP_NODE], "fix": [False, True, True]})
    node = sessions.request(sid, "inspect_node", {"node_id": TIP_NODE, "variables": ["DISPLACEMENT"]})
    assert node["fixed"]["DISPLACEMENT"] == [False, True, True]
    sessions.request(sid, "advance", {"steps": 2})
    pinned = _tip_uy(sid)
    assert pinned == pytest.approx(free, rel=1e-6)   # held at the step-1 value, not 3*free
    sessions.close(sid)


def test_exec_python_and_errors(case, monkeypatch):
    sid = sessions.start(str(case), timeout=120)["session_id"]
    out = sessions.request(sid, "exec", {"code": "print('hello')\nresult = model['Structure'].NumberOfNodes()"})
    assert out["result"] == 105 and out["stdout"].strip() == "hello"
    with pytest.raises(sessions.SessionError, match="Unknown variable"):
        sessions.request(sid, "field_summary", {"variable": "NOT_A_VARIABLE"})
    with pytest.raises(sessions.SessionError, match="Node 99999 not found"):
        sessions.request(sid, "inspect_node", {"node_id": 99999})
    assert sessions.status(sid)["state"] == "ready"   # errors never kill the session
    sessions.close(sid)


def test_exec_refused_in_worker_without_env(case, monkeypatch):
    monkeypatch.delenv("KRATOS_MCP_ALLOW_EXEC")
    sid = sessions.start(str(case), timeout=120)["session_id"]
    with pytest.raises(sessions.SessionError, match="disabled"):
        sessions.request(sid, "exec", {"code": "result = 1"})
    sessions.close(sid)


def test_multistage_and_mpi_cases_are_rejected(case):
    import json
    pp = json.loads((case / "ProjectParameters.json").read_text())
    pp["problem_data"]["parallel_type"] = "MPI"
    (case / "ProjectParameters.json").write_text(json.dumps(pp))
    with pytest.raises(sessions.SessionError, match="serial/OpenMP"):
        sessions.start(str(case), timeout=120)
    ms = EXAMPLES_DIR / "multistage_load_steps"
    with pytest.raises(sessions.SessionError, match="Multi-stage"):
        sessions.start(str(ms), timeout=120)
