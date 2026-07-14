from __future__ import annotations

import json
import re
from pathlib import Path

from kratos_mcp import flowgraph, project_explain

EXAMPLES = Path(__file__).resolve().parent.parent / "src" / "kratos_mcp" / "examples"


def _load(path: Path) -> dict:
    text = re.sub(r"//[^\n]*", "", path.read_text())
    return json.loads(text)


def _cantilever() -> dict:
    return _load(EXAMPLES / "cantilever" / "ProjectParameters.json")


def _naca() -> dict:
    return _load(EXAMPLES / "naca_airfoil" / "ProjectParameters.json")


_MULTISTAGE = {
    "orchestrator": {
        "name": "Orchestrators.KratosMultiphysics.SequentialOrchestrator",
        "settings": {"echo_level": 0, "execution_list": ["s1", "s2"],
                     "load_from_checkpoint": None, "stage_checkpoints": False},
    },
    "stages": {
        "s1": {"stage_settings": {
            "problem_data": {"problem_name": "a", "start_time": 0.0, "end_time": 1.0},
            "solver_settings": {"solver_type": "Static", "model_part_name": "Structure",
                                "model_import_settings": {"input_type": "mdpa", "input_filename": "mesh"},
                                "linear_solver_settings": {"solver_type": "LinearSolversApplication.sparse_lu"}},
            "processes": {"constraints_process_list": [], "loads_process_list": []},
            "output_processes": {},
            "analysis_stage": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis"}},
        "s2": {"stage_settings": {
            "problem_data": {"problem_name": "b", "start_time": 1.0, "end_time": 2.0},
            "solver_settings": {"solver_type": "Static", "model_part_name": "Structure",
                                "model_import_settings": {"input_type": "use_input_model_part"},
                                "linear_solver_settings": {"solver_type": "LinearSolversApplication.sparse_lu"}},
            "processes": {}, "output_processes": {},
            "analysis_stage": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis"}},
    },
}


# --- project_explain --------------------------------------------------------

def test_explain_single_stage_structural():
    summary = project_explain.explain(_cantilever())
    assert summary["kind"] == "single_stage"
    assert summary["analysis_type"] == "structural"
    assert summary["solver"]["solver_type"] == "Static"
    assert summary["solver"]["model_part_name"] == "Structure"
    assert len(summary["processes"]) >= 2


def test_explain_single_stage_fluid():
    summary = project_explain.explain(_naca())
    assert summary["analysis_type"] == "fluid"
    assert summary["solver"]["solver_type"] == "Monolithic"


def test_explain_multistage():
    summary = project_explain.explain(_MULTISTAGE)
    assert summary["kind"] == "multi_stage"
    assert summary["orchestrator"] == "SequentialOrchestrator"
    assert summary["execution_list"] == ["s1", "s2"]
    assert [s["name"] for s in summary["stages"]] == ["s1", "s2"]
    assert summary["stages"][0]["analysis_type"] == "structural"


# --- Flowgraph round-trip ---------------------------------------------------

def _roundtrip_exact(params: dict) -> bool:
    graph = flowgraph.project_to_graph(params)
    back = flowgraph.graph_to_project(graph)
    return json.dumps(params, sort_keys=True) == json.dumps(back, sort_keys=True)


def test_flowgraph_roundtrip_cantilever():
    assert _roundtrip_exact(_cantilever())


def test_flowgraph_roundtrip_naca():
    assert _roundtrip_exact(_naca())


def test_flowgraph_roundtrip_multistage():
    assert _roundtrip_exact(_MULTISTAGE)


def test_exported_graph_shape():
    graph = flowgraph.project_to_graph(_cantilever())
    assert set(graph) >= {"nodes", "links", "version", "last_node_id", "last_link_id"}
    assert graph["last_node_id"] == len(graph["nodes"])
    for node in graph["nodes"]:
        assert {"id", "type", "pos", "properties"} <= set(node)
        assert node["properties"]["_role"]
