"""CoSimulation (coupled-solver) support: detection, static validation,
scaffolding, rejections where unsupported, and log parsing. No Kratos needed;
the real run is in tests/test_examples.py (kratos-marked)."""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from kratos_mcp import checkpoints, cosim, flowgraph, logparse, mpi_launch, project_explain, studies
from kratos_mcp.tools import interop, scaffold
from kratos_mcp.tools.resources import EXAMPLES_DIR

CASE = EXAMPLES_DIR / "coupled_sdof"


def _tools(module):
    captured: dict = {}

    class FakeMCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    module.register(FakeMCP())
    return captured


@pytest.fixture
def case(tmp_path):
    dest = tmp_path / "case"
    shutil.copytree(CASE, dest)
    return dest


def _load(case):
    return json.loads((case / "ProjectParameters.json").read_text())


def _save(case, params):
    (case / "ProjectParameters.json").write_text(json.dumps(params))


# --- detection ---------------------------------------------------------------

def test_is_cosim_detection():
    assert cosim.is_cosim(_load(CASE))
    assert cosim.is_cosim({"solver_settings": {"type": "coupled_solvers.jacobi_weak"}})
    assert not cosim.is_cosim({"solver_settings": {"solver_type": "Static"}})
    assert not cosim.is_cosim({"orchestrator": {}, "stages": {}})
    assert not cosim.is_cosim(None)


# --- validation --------------------------------------------------------------

def test_example_is_valid():
    result = scaffold.validate_case_files(CASE, deep=True)
    assert result["valid"], result
    assert result["issues"] == []
    assert result["deep_validated"] is False  # SDoF has no Kratos-side check


def _break_unknown_solver(p):
    p["solver_settings"]["coupling_sequence"][1]["name"] = "ghost"


def _break_unknown_data(p):
    p["solver_settings"]["coupling_sequence"][0]["output_data_list"][0]["data"] = "nope"


def _break_far_data(p):
    p["solver_settings"]["coupling_sequence"][0]["output_data_list"][0]["to_solver_data"] = "nope"


def _break_operator(p):
    p["solver_settings"]["coupling_sequence"][0]["output_data_list"][0]["data_transfer_operator"] = "mapper"


def _break_far_solver(p):
    p["solver_settings"]["coupling_sequence"][1]["input_data_list"] = [{
        "data": "root_disp", "from_solver": "ghost", "from_solver_data": "disp",
        "data_transfer_operator": "copy"}]


def _break_criterion_data(p):
    p["solver_settings"]["convergence_criteria"][0]["data_name"] = "nope"


def _break_no_criteria(p):
    p["solver_settings"]["convergence_criteria"] = []


def _break_variable(p):
    p["solver_settings"]["solvers"]["sdof_1"]["data"]["disp"]["variable_name"] = "DISPLACEMENT"


def _break_location(p):
    del p["solver_settings"]["solvers"]["sdof_1"]["data"]["disp"]["location"]


def _break_input_file(p):
    p["solver_settings"]["solvers"]["sdof_1"]["solver_wrapper_settings"]["input_file"] = "missing"


@pytest.mark.parametrize("mutate,message", [
    (_break_unknown_solver, "unknown solver 'ghost'"),
    (_break_unknown_data, "data 'nope' is not declared in solver 'sdof_1'"),
    (_break_far_data, "to_solver_data 'nope' is not declared in solver 'sdof_2'"),
    (_break_operator, "data_transfer_operator 'mapper' is not defined"),
    (_break_far_solver, "from_solver 'ghost' is not a declared solver"),
    (_break_criterion_data, "data_name 'nope' is not declared"),
    (_break_no_criteria, "needs at least one convergence_criteria"),
    (_break_variable, "only allows"),
    (_break_location, "must set \"location\": \"model_part\""),
    (_break_input_file, "input file missing.json not found"),
])
def test_invalid_configurations_are_diagnosed(case, mutate, message):
    params = _load(case)
    mutate(params)
    _save(case, params)
    result = scaffold.validate_case_files(case, deep=False)
    assert not result["valid"]
    assert any(message in issue for issue in result["issues"]), result["issues"]


def test_solver_missing_from_sequence(case):
    params = _load(case)
    params["solver_settings"]["coupling_sequence"].pop()
    # the dangling coupling inputs go with it
    params["solver_settings"]["coupling_sequence"][0]["input_data_list"] = []
    params["solver_settings"]["coupling_sequence"][0]["output_data_list"] = []
    _save(case, params)
    result = scaffold.validate_case_files(case, deep=False)
    assert any("missing from coupling_sequence" in i for i in result["issues"])


def test_sdof_input_file_unknown_key(case):
    sdof = json.loads((case / "sdof_1.json").read_text())
    sdof["system_parameters"]["weight"] = 3.0
    (case / "sdof_1.json").write_text(json.dumps(sdof))
    result = scaffold.validate_case_files(case, deep=False)
    assert any("unknown key 'system_parameters.weight'" in i for i in result["issues"])


def test_kratos_wrapper_subcase_validated_through_callback(case):
    params = _load(case)
    params["solver_settings"]["solvers"]["sdof_1"]["type"] = "solver_wrappers.kratos.structural_mechanics_wrapper"
    seen = []

    def callback(name, path, solver):
        seen.append((name, path.name))
        return {"issues": ["mesh Foo.mdpa not found"], "warnings": []}

    result = cosim.validate(case, params, callback)
    assert seen == [("sdof_1", "sdof_1.json")]
    assert "solver 'sdof_1': mesh Foo.mdpa not found" in result["issues"]


# --- scaffolding -------------------------------------------------------------

def _solver_specs():
    return [
        {"name": "a", "type": "sdof", "parameters": {"system_parameters": {"mass": 1.0}},
         "data": {"disp": {"variable_name": "SCALAR_DISPLACEMENT"},
                  "force": {"variable_name": "SCALAR_FORCE"}}},
        {"name": "b", "type": "sdof", "parameters": {"system_parameters": {"mass": 2.0}},
         "data": {"root": {"variable_name": "SCALAR_ROOT_POINT_DISPLACEMENT"},
                  "reaction": {"variable_name": "SCALAR_REACTION"}}},
    ]


COUPLINGS = [
    {"from_solver": "a", "from_data": "disp", "to_solver": "b", "to_data": "root"},
    {"from_solver": "b", "from_data": "reaction", "to_solver": "a", "to_data": "force"},
]


def test_create_cosim_project_shape(tmp_path):
    tools = _tools(scaffold)
    res = tools["create_cosim_project"](
        directory=str(tmp_path / "c"), solvers=_solver_specs(), couplings=COUPLINGS,
        coupled_solver="gauss_seidel_strong",
        convergence_criteria=[{"type": "relative_norm_initial_residual", "solver": "a",
                               "data_name": "force", "abs_tolerance": 1e-9, "rel_tolerance": 1e-6}],
        convergence_accelerators=[{"type": "aitken", "solver": "a", "data_name": "force"}])
    assert "error" not in res, res
    assert res["validation"]["valid"], res["validation"]
    assert res["coupling_order"] == ["a", "b"]
    names = {p.rsplit("/", 1)[-1] for p in res["created"]}
    assert names == {"ProjectParameters.json", "a.json", "b.json"}
    params = json.loads((tmp_path / "c" / "ProjectParameters.json").read_text())
    settings = params["solver_settings"]
    assert settings["type"] == "coupled_solvers.gauss_seidel_strong"
    first, second = settings["coupling_sequence"]
    # producer-first coupling is an output of 'a'; producer-last is an input of 'a'
    assert first["output_data_list"][0]["to_solver"] == "b"
    assert first["input_data_list"][0]["from_solver"] == "b"
    assert second["input_data_list"] == [] and second["output_data_list"] == []
    # shorthand fills the SDoF model part and location
    disp = settings["solvers"]["a"]["data"]["disp"]
    assert disp["model_part_name"] == "Sdof" and disp["location"] == "model_part"


def test_create_cosim_project_weak_has_no_strong_keys(tmp_path):
    tools = _tools(scaffold)
    res = tools["create_cosim_project"](
        directory=str(tmp_path / "c"), solvers=_solver_specs(), couplings=COUPLINGS)
    assert "error" not in res, res
    settings = json.loads((tmp_path / "c" / "ProjectParameters.json").read_text())["solver_settings"]
    assert settings["type"] == "coupled_solvers.gauss_seidel_weak"
    assert "convergence_criteria" not in settings


def test_create_cosim_project_reports_invalid_coupling(tmp_path):
    tools = _tools(scaffold)
    bad = [dict(COUPLINGS[0], to_data="nope"), COUPLINGS[1]]
    res = tools["create_cosim_project"](
        directory=str(tmp_path / "c"), solvers=_solver_specs(), couplings=bad)
    assert not res["validation"]["valid"]
    assert any("to_solver_data 'nope'" in i for i in res["validation"]["issues"])


@pytest.mark.parametrize("kwargs,message", [
    ({"solvers": []}, "at least one solver"),
    ({"couplings": []}, "at least one coupling"),
    ({"solvers": [{"name": "a.b", "type": "sdof", "parameters": {}}]}, "without dots"),
    ({"solvers": [{"name": "a", "parameters": {}}]}, "needs a 'type'"),
    ({"solvers": [{"name": "a", "type": "sdof"}]}, "needs 'parameters' or 'input_file'"),
    ({"couplings": [{"from_solver": "a", "from_data": "d", "to_solver": "zzz", "to_data": "e"}]},
     "Invalid coupling"),
])
def test_create_cosim_project_input_errors(tmp_path, kwargs, message):
    tools = _tools(scaffold)
    args = {"directory": str(tmp_path / "c"), "solvers": _solver_specs(), "couplings": COUPLINGS}
    args.update(kwargs)
    assert message in tools["create_cosim_project"](**args)["error"]


# --- rejections where cosim is unsupported -----------------------------------

def test_mpi_rejected():
    params = _load(CASE)
    params["problem_data"]["parallel_type"] = "MPI"
    with pytest.raises(ValueError, match="CoSimulation"):
        mpi_launch.validate_request(params, 2, None)


def test_studies_rejected():
    with pytest.raises(ValueError, match="CoSimulation"):
        studies.serial_case(_load(CASE))


def test_checkpoints_rejected():
    with pytest.raises(ValueError, match="CoSimulation"):
        checkpoints.serial_single(_load(CASE))


def test_flowgraph_export_refused(tmp_path):
    with pytest.raises(ValueError, match="CoSimulation"):
        flowgraph.project_to_graph(_load(CASE))
    tools = _tools(interop)
    result = tools["export_case_to_flowgraph"](str(CASE / "ProjectParameters.json"))
    assert "CoSimulation" in result["error"]


def test_explain_cosim():
    summary = project_explain.explain(_load(CASE))
    assert summary["kind"] == "cosimulation"
    assert summary["coupled_solver"] == "coupled_solvers.gauss_seidel_strong"
    assert summary["coupling_order"] == ["sdof_1", "sdof_2"]
    assert {"from": "sdof_1.disp", "to": "sdof_2.root_disp", "operator": "copy",
            "options": []} in summary["transfers"]
    assert {"from": "sdof_2.reaction", "to": "sdof_1.force", "operator": "copy",
            "options": []} in summary["transfers"]
    assert summary["solvers"]["sdof_1"]["input_file"] == "sdof_1"


# --- log parsing (real CoSimulationAnalysis output) --------------------------

COSIM_LOG = """\
time=0.01 | step=1:
GaussSeidelStrongCoupledSolver: Coupling iteration: 1 / 30
GaussSeidelStrongCoupledSolver: ### CONVERGENCE WAS ACHIEVED ###

time=0.02 | step=2:
GaussSeidelStrongCoupledSolver: Coupling iteration: 1 / 30
[WARNING] AitkenConvergenceAccelerator: dynamic relaxation factor reaches lower bound: -2.0
GaussSeidelStrongCoupledSolver: Coupling iteration: 2 / 30
GaussSeidelStrongCoupledSolver: XXX CONVERGENCE WAS NOT ACHIEVED XXX
"""


def test_logparse_cosim_progress_and_convergence():
    progress = logparse.progress(COSIM_LOG)
    assert progress["current_step"] == 2 and progress["current_time"] == 0.02
    assert progress["num_steps_seen"] == 2
    conv = logparse.convergence(COSIM_LOG)
    assert [s["converged"] for s in conv["steps"]] == [True, False]
    assert [s["iterations"] for s in conv["steps"]] == [1, 2]
    assert conv["steps"][0]["time"] == 0.01
    assert conv["max_iterations_in_a_step"] == 2
