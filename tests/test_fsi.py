"""Partitioned FSI (FSIApplication) support: detection, static validation,
scaffolding, rejections where unsupported, and log parsing. No Kratos needed;
the real runs are in tests/test_examples.py (kratos-marked)."""

from __future__ import annotations

import json

import pytest

from kratos_mcp import checkpoints, flowgraph, fsi, logparse, mdpa, mpi_launch, project_explain, studies
from kratos_mcp.tools import resources as resources_mod
from kratos_mcp.tools import scaffold
from kratos_mcp.tools.resources import EXAMPLES_DIR

CASE = EXAMPLES_DIR / "fsi_flexible_flap"


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


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    directory = tmp_path_factory.mktemp("fsi") / "case"
    result = _tools(scaffold)["create_fsi_project"](str(directory))
    assert "error" not in result, result
    return directory, result


@pytest.fixture
def case(generated, tmp_path):
    import shutil

    dest = tmp_path / "case"
    shutil.copytree(generated[0], dest)
    return dest


def _load(case):
    return json.loads((case / "ProjectParameters.json").read_text())


def _save(case, params):
    (case / "ProjectParameters.json").write_text(json.dumps(params))


def _check(case):
    return scaffold.validate_case_files(case, deep=False)


# --- detection ---------------------------------------------------------------

def test_is_fsi_detection():
    params = json.loads((CASE / "ProjectParameters.json").read_text())
    assert fsi.is_fsi(params)
    assert fsi.is_fsi({"solver_settings": {"solver_type": "Partitioned"}})
    assert fsi.is_fsi({"solver_settings": {"solver_type": "partitioned_embedded"}})
    assert not fsi.is_fsi({"solver_settings": {"solver_type": "Static"}})
    assert not fsi.is_fsi({"solver_settings": {"type": "coupled_solvers.gauss_seidel_weak"}})
    assert not fsi.is_fsi({"orchestrator": {}, "stages": {}})
    assert not fsi.is_fsi(None)


# --- the shipped example and the generated case -----------------------------

def test_example_files_and_validation():
    fluid = mdpa.read(CASE / "fluid.mdpa")
    structure = mdpa.read(CASE / "structure.mdpa")
    assert fluid.validate() == [] and structure.validate() == []
    assert len(fluid.nodes) == 4556 and len(structure.nodes) == 505
    ref = json.loads((CASE / "reference_control_nodes.json").read_text())
    assert set(ref) == {"TIME", "NODE_2122", "NODE_3100"}
    result = _check(CASE)
    assert result["valid"], result
    assert result["warnings"] == []
    assert result["required_applications"] == list(fsi.REQUIRED_APPLICATIONS)


def test_generated_case_is_valid_with_non_matching_interfaces(generated):
    directory, result = generated
    assert result["validation"]["valid"], result["validation"]
    assert result["interface"] == {"fluid": "FluidModelPart.Interface", "structure": "Structure.Interface"}
    fluid = mdpa.read(directory / "fluid.mdpa")
    wall = mdpa.read(directory / "structure.mdpa")
    assert fluid.validate() == [] and wall.validate() == []
    # The point of the generated case: the interface discretizations differ.
    assert len(fluid.sub_model_parts["Interface"].nodes) == 31
    assert len(wall.sub_model_parts["Interface"].nodes) == 21
    # ...and describe the same line y = 0.
    assert {fluid.nodes[i][1] for i in fluid.sub_model_parts["Interface"].nodes} == {0.0}
    assert {wall.nodes[i][1] for i in wall.sub_model_parts["Interface"].nodes} == {0.0}
    assert wall.bounding_box()["min"][1] == pytest.approx(-0.4)
    params = json.loads((directory / "ProjectParameters.json").read_text())
    assert params["solver_settings"]["structure_solver_settings"]["solver_type"] == "Static"
    for name in ("MaterialsFluid.json", "MaterialsStructure.json"):
        json.loads((directory / name).read_text())


def test_create_fsi_project_options(tmp_path):
    tool = _tools(scaffold)["create_fsi_project"]
    result = tool(str(tmp_path / "dyn"), dynamic_structure=True, fluid_divisions=[8, 4],
                  wall_divisions=[5, 2], accelerator="Relaxation", end_time=0.5)
    assert result["validation"]["valid"], result["validation"]
    params = json.loads((tmp_path / "dyn" / "ProjectParameters.json").read_text())
    structure = params["solver_settings"]["structure_solver_settings"]
    assert structure["solver_type"] == "Dynamic" and structure["scheme_type"] == "bossak"
    assert params["solver_settings"]["coupling_settings"]["coupling_strategy_settings"]["solver_type"] == "Relaxation"
    assert params["problem_data"]["end_time"] == 0.5


@pytest.mark.parametrize("kwargs,message", [
    ({"fluid_divisions": [4]}, "fluid_divisions"),
    ({"length": -1.0}, "length must be positive"),
    ({"poisson_ratio": 0.5}, "poisson_ratio"),
    ({"accelerator": "bogus"}, "Unknown accelerator"),
    ({"end_time": 0.0}, "end_time"),
])
def test_create_fsi_project_rejects_bad_input(tmp_path, kwargs, message):
    result = _tools(scaffold)["create_fsi_project"](str(tmp_path / "x"), **kwargs)
    assert message in result["error"]
    assert not (tmp_path / "x").exists()


# --- validation --------------------------------------------------------------

def _solver(params):
    return params["solver_settings"]


def _bare_interface(params):
    coupling = _solver(params)["coupling_settings"]
    coupling["fluid_interfaces_list"] = ["Interface"]
    coupling["mapper_settings"][0]["fluid_interface_submodelpart_name"] = "Interface"


def _unknown_interface(params):
    coupling = _solver(params)["coupling_settings"]
    coupling["structure_interfaces_list"] = ["Structure.Nope"]
    coupling["mapper_settings"][0]["structure_interface_submodelpart_name"] = "Structure.Nope"


def _interface_not_listed(params):
    _solver(params)["coupling_settings"]["fluid_interfaces_list"] = ["FluidModelPart.Inlet"]


def _two_mappers_wrong_faces(params):
    mappers = _solver(params)["coupling_settings"]["mapper_settings"]
    mappers.append(dict(mappers[0]))


def _no_mapper(params):
    _solver(params)["coupling_settings"]["mapper_settings"] = []


def _domain_size(params):
    _solver(params)["structure_solver_settings"]["domain_size"] = 3


def _same_root(params):
    _solver(params)["structure_solver_settings"]["model_part_name"] = "FluidModelPart"


def _mesh_solver_part(params):
    _solver(params)["mesh_solver_settings"]["model_part_name"] = "Structure"


def _missing_mesh_block(params):
    del _solver(params)["mesh_solver_settings"]


def _bad_scheme(params):
    _solver(params)["coupling_scheme"] = "neumann_neumann"


def _bad_accelerator(params):
    _solver(params)["coupling_settings"]["coupling_strategy_settings"]["solver_type"] = "Anderson"


def _bad_iterations(params):
    _solver(params)["coupling_settings"]["nl_max_it"] = 0


def _bad_tolerance(params):
    _solver(params)["coupling_settings"]["nl_tol"] = -1.0


def _unknown_skin_part(params):
    _solver(params)["fluid_solver_settings"]["skin_parts"].append("FluidModelPart.Ghost")


def _unknown_process_part(params):
    params["processes"]["fluid_boundary_conditions_process_list"][0]["Parameters"]["model_part_name"] = \
        "FluidModelPart.Ghost"


def _process_wrong_root(params):
    params["processes"]["structure_constraints_process_list"][0]["Parameters"]["model_part_name"] = "Nowhere.Fixed"


def _mpi(params):
    params["problem_data"]["parallel_type"] = "MPI"


def _restart(params):
    params["restart_settings"] = {}


@pytest.mark.parametrize("mutate,message", [
    (_bare_interface, "must be a full model part name"),
    (_unknown_interface, "does not match any SubModelPart of the structure mesh"),
    (_interface_not_listed, "is not in fluid_interfaces_list"),
    (_two_mappers_wrong_faces, "'Positive' and 'Negative'"),
    (_no_mapper, "mapper_settings must list"),
    (_domain_size, "domain_size differs"),
    (_same_root, "same model_part_name"),
    (_mesh_solver_part, "mesh_solver_settings.model_part_name must be the fluid model part"),
    (_missing_mesh_block, "mesh_solver_settings is missing"),
    (_bad_scheme, "coupling_scheme"),
    (_bad_accelerator, "solver_type 'Anderson' is unknown"),
    (_bad_iterations, "nl_max_it"),
    (_bad_tolerance, "nl_tol"),
    (_unknown_skin_part, "skin_parts"),
    (_unknown_process_part, "does not match any SubModelPart of the fluid mesh"),
    (_process_wrong_root, "must start with the structure model part"),
    (_mpi, "OpenMP"),
    (_restart, "restart"),
])
def test_invalid_configurations_are_diagnosed(case, mutate, message):
    params = _load(case)
    mutate(params)
    _save(case, params)
    result = _check(case)
    assert not result["valid"]
    assert any(message in issue for issue in result["issues"]), result["issues"]


def test_missing_interface_is_diagnosed_before_launch(case):
    """The roadmap probe: a missing interface sub model part never reaches Kratos."""
    wall = mdpa.read(case / "structure.mdpa")
    del wall.sub_model_parts["Interface"]
    wall.write(case / "structure.mdpa")
    result = _check(case)
    assert not result["valid"]
    assert any("Structure.Interface" in issue and "SubModelPart" in issue for issue in result["issues"])


def test_missing_files_are_diagnosed(case):
    (case / "MaterialsStructure.json").unlink()
    (case / "fluid.mdpa").unlink()
    issues = " | ".join(_check(case)["issues"])
    assert "materials file MaterialsStructure.json not found" in issues
    assert "mesh file fluid.mdpa not found" in issues


def test_interfaces_in_different_places_warn(case):
    wall = mdpa.read(case / "structure.mdpa")
    wall.nodes = {i: (x, y - 5.0, z) for i, (x, y, z) in wall.nodes.items()}
    wall.write(case / "structure.mdpa")
    result = _check(case)
    assert result["valid"]
    assert any("do not occupy the same region" in w for w in result["warnings"])


def test_embedded_variant_needs_no_mesh_solver(case):
    params = _load(case)
    _solver(params)["solver_type"] = "partitioned_embedded"
    del _solver(params)["mesh_solver_settings"]
    _save(case, params)
    assert _check(case)["valid"]


def test_deep_validation_reports_missing_applications(case, monkeypatch):
    from kratos_mcp import bridge

    monkeypatch.setattr(bridge, "run_op", lambda op, args=None: ["FluidDynamicsApplication"])
    result = scaffold.validate_case_files(case, deep=True)
    assert not result["valid"] and not result["deep_validated"]
    assert any("FSIApplication" in i and "not available" in i for i in result["issues"])

    monkeypatch.setattr(bridge, "run_op", lambda op, args=None: list(fsi.REQUIRED_APPLICATIONS))
    result = scaffold.validate_case_files(case, deep=True)
    assert result["valid"] and result["deep_validated"]

    def unavailable(op, args=None):
        raise bridge.BridgeError("no kratos")

    monkeypatch.setattr(bridge, "run_op", unavailable)
    result = scaffold.validate_case_files(case, deep=True)
    assert result["valid"] and not result["deep_validated"]
    assert any("not checked" in w for w in result["warnings"])


# --- explain, rejections -----------------------------------------------------

def test_explain_summarizes_fsi(case):
    summary = project_explain.explain(_load(case))
    assert summary["kind"] == "fsi"
    assert summary["fluid"]["solver_type"] == "Monolithic"
    assert summary["structure"]["model_part_name"] == "Structure"
    assert summary["mesh_motion"]["solver_type"] == "mesh_solver_structural_similarity"
    assert summary["coupling"]["accelerator"] == "MVQN_recursive"
    assert summary["coupling"]["fluid_interfaces"] == ["FluidModelPart.Interface"]
    assert summary["required_applications"] == list(fsi.REQUIRED_APPLICATIONS)


def test_unsupported_features_reject_fsi(case):
    params = _load(case)
    with pytest.raises(ValueError, match="FSI"):
        mpi_launch.validate_request(params, 2, None)
    with pytest.raises(ValueError, match="FSI"):
        studies.serial_case(params)
    with pytest.raises(ValueError, match="FSI"):
        checkpoints.serial_single(params)
    with pytest.raises(ValueError, match="FSI"):
        flowgraph.project_to_graph(params)


# --- resource ---------------------------------------------------------------

def test_example_resource_renders():
    captured = {}

    class FakeMCP:
        def resource(self, uri):
            def deco(fn):
                captured[uri] = fn
                return fn
            return deco

    resources_mod.register(FakeMCP())
    text = captured["kratos://examples/fsi-flexible-flap"]()
    assert "partitioned FSI" in text and "create_fsi_project" in text
    block = text.split("## ProjectParameters.json\n\n```json\n", 1)[1].split("\n```", 1)[0]
    assert fsi.is_fsi(json.loads(block))


# --- logs -------------------------------------------------------------------

LOG = """\
fsi_case: STEP =  1
fsi_case: TIME =  0.1
PartitionedFSIBaseSolver: FSI non-linear iteration = 1 |res|/sqrt(nDOFS) = 1.4e-05
PartitionedFSIBaseSolver: FSI non-linear iteration = 2 |res|/sqrt(nDOFS) = 4.7e-04
PartitionedFSIBaseSolver: FSI non-linear iteration = 3 |res|/sqrt(nDOFS) = 5.2e-07
PartitionedFSIBaseSolver: FSI non-linear iteration convergence achieved in 3 iterations.
fsi_case: STEP =  2
fsi_case: TIME =  0.2
PartitionedFSIBaseSolver: FSI non-linear iteration = 1 |res|/sqrt(nDOFS) = 9.3e-05
PartitionedFSIBaseSolver: FSI non-linear iteration = 2 |res|/sqrt(nDOFS) = nan
[WARNING] PartitionedFSIBaseSolver: FSI non-linear maximum iterations 2 reached
fsi_case: Analysis -END-
"""


def test_logparse_understands_fsi_logs():
    progress = logparse.progress(LOG)
    assert progress["current_step"] == 2 and progress["current_time"] == pytest.approx(0.2)
    assert progress["num_steps_seen"] == 2
    conv = logparse.convergence(LOG)
    assert conv["num_steps"] == 2
    assert conv["num_converged"] == 1 and conv["num_not_converged"] == 1
    first, second = conv["steps"]
    assert first["iterations"] == 3 and first["converged"] is True and first["time"] == pytest.approx(0.1)
    assert first["residual_ratios"] == [1.4e-05, 4.7e-04, 5.2e-07]
    assert second["converged"] is False and second["residual_ratios"] == [9.3e-05]  # nan is dropped


def test_logparse_unchanged_for_analysis_stage_logs():
    text = "STEP: 1\nTIME: 0.5\nITERATION:  2\nConvergence is achieved after 2 iterations\n"
    conv = logparse.convergence(text)
    assert conv["steps"][0]["converged"] is True and conv["steps"][0]["iterations"] == 2

