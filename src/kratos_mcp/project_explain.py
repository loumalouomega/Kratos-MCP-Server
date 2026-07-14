"""Structured explanation of an existing Kratos ProjectParameters.json.

Pure-Python JSON analysis (no Kratos import): given a parsed ProjectParameters
dict, deduce and summarize its structure -- analysis type, solver, linear
solver, mesh/material import, and the flattened process/output-process lists --
so an assistant can understand a case it did not scaffold. Multi-stage
(orchestrator/stages) cases are summarized per stage. The solver_type ->
analysis-type / node deduction mirrors Flowgraph's load_project_parameters.js
round-trip importer.

This is the foundation for flowgraph.py (Flowgraph graph interop), which maps
the same decomposition onto litegraph node types."""

from __future__ import annotations

from typing import Any

# solver_type prefix -> analysis type (aligned with runner._SOLVER_TYPE_HINTS).
_ANALYSIS_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("static", "dynamic", "eigen_value", "prebuckling", "harmonic", "formfinding"), "structural"),
    (("monolithic", "fractional_step", "embedded", "compressible", "two_fluids", "low_mach"), "fluid"),
    (("transient", "stationary", "conjugate", "thermal"), "thermal"),
    (("potential_flow", "ale_potential_flow"), "potential_flow"),
]

# analysis_stage module hints -> analysis type (used when solver_type is absent).
_STAGE_MODULE_HINTS: list[tuple[str, str]] = [
    ("StructuralMechanicsApplication", "structural"),
    ("FluidDynamicsApplication", "fluid"),
    ("ConvectionDiffusionApplication", "thermal"),
    ("CompressiblePotentialFlowApplication", "potential_flow"),
]


def detect_analysis_type(solver_type: str, analysis_stage: str = "") -> str | None:
    st = (solver_type or "").lower()
    for prefixes, analysis in _ANALYSIS_HINTS:
        if any(st.startswith(p) for p in prefixes):
            return analysis
    for needle, analysis in _STAGE_MODULE_HINTS:
        if needle in (analysis_stage or ""):
            return analysis
    return None


def _flatten_processes(process_container: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a processes / output_processes container (list-name -> [blocks])
    into a flat list of {list, python_module, process_name, model_parts, params}."""
    out: list[dict[str, Any]] = []
    if not isinstance(process_container, dict):
        return out
    for list_name, blocks in process_container.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            params = block.get("Parameters", {})
            model_parts = []
            if isinstance(params, dict):
                for key in ("model_part_name", "far_field_model_part_name"):
                    if isinstance(params.get(key), str):
                        model_parts.append(params[key])
            out.append({
                "list": list_name,
                "python_module": block.get("python_module"),
                "kratos_module": block.get("kratos_module"),
                "process_name": block.get("process_name"),
                "model_parts": model_parts,
            })
    return out


def _explain_single(params: dict[str, Any]) -> dict[str, Any]:
    """Summarize a single-stage ProjectParameters (or one stage's settings)."""
    problem = params.get("problem_data", {})
    solver = params.get("solver_settings", {})
    solver_type = solver.get("solver_type", "")
    analysis_stage = params.get("analysis_stage", "")

    linear = solver.get("linear_solver_settings")
    # Fractional-step carries velocity/pressure linear solvers instead.
    if linear is None:
        linear = solver.get("velocity_linear_solver_settings")

    summary: dict[str, Any] = {
        "analysis_type": detect_analysis_type(solver_type, analysis_stage),
        "analysis_stage": analysis_stage or None,
        "problem_name": problem.get("problem_name"),
        "parallel_type": problem.get("parallel_type"),
        "start_time": problem.get("start_time"),
        "end_time": problem.get("end_time"),
        "solver": {
            "solver_type": solver_type or None,
            "model_part_name": solver.get("model_part_name"),
            "domain_size": solver.get("domain_size"),
        },
        "linear_solver": {"solver_type": linear.get("solver_type")} if isinstance(linear, dict) else None,
        "model_import": solver.get("model_import_settings"),
        "materials": solver.get("material_import_settings"),
        "processes": _flatten_processes(params.get("processes", {})),
        "output_processes": _flatten_processes(params.get("output_processes", {})),
    }
    return summary


def explain(params: dict[str, Any]) -> dict[str, Any]:
    """Return a structured summary of a ProjectParameters dict. Handles both
    single-stage cases and multi-stage (orchestrator/stages) cases."""
    if "orchestrator" in params and "stages" in params:
        orch = params["orchestrator"]
        settings = orch.get("settings", {}) if isinstance(orch, dict) else {}
        stages = params.get("stages", {})
        stage_summaries = []
        for name, stage in stages.items():
            stage_settings = stage.get("stage_settings", {}) if isinstance(stage, dict) else {}
            item = {"name": name}
            item.update(_explain_single(stage_settings))
            stage_summaries.append(item)
        return {
            "kind": "multi_stage",
            "orchestrator": (orch.get("name", "") if isinstance(orch, dict) else "").split(".")[-1],
            "execution_list": settings.get("execution_list", []),
            "num_stages": len(stages),
            "stages": stage_summaries,
        }
    result = {"kind": "single_stage"}
    result.update(_explain_single(params))
    return result
