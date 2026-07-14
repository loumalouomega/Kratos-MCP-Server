"""Convert a Kratos ProjectParameters.json to/from a Flowgraph (litegraph)
graph.json and back, losslessly.

Flowgraph (github.com/loumalouomega/Flowgraph) is a browser node-graph editor
for Kratos cases; it saves the canvas as a litegraph ``graph.serialize()``
object: ``{nodes, links, version, last_node_id, last_link_id}``. Each node here
is emitted with a Flowgraph-compatible ``type`` string and column position (so
the graph loads and lays out sensibly in the editor), and additionally carries
its exact ProjectParameters fragment in ``properties`` under an underscore-
prefixed ``_role`` marker. Reconstruction (graph_to_project) reads those
markers -- never the links -- so ``import(export(p))`` reproduces ``p`` exactly.

Links are emitted best-effort for visual wiring only. Pure JSON; no Kratos."""

from __future__ import annotations

from typing import Any

from . import project_explain

# Solver-settings sub-blocks that Flowgraph models as their own nodes.
_SOLVER_COMPONENT_KEYS = (
    "linear_solver_settings",
    "velocity_linear_solver_settings",
    "pressure_linear_solver_settings",
    "model_import_settings",
    "material_import_settings",
)

_COLUMN_X = {
    "linear_solver_settings": 0,
    "velocity_linear_solver_settings": 0,
    "pressure_linear_solver_settings": 0,
    "model_import_settings": 0,
    "material_import_settings": 0,
    "problem_data": 1,
    "solver_settings": 1,
    "process": 2,
    "output_process": 2,
    "stage": 2,
    "orchestrator": 3,
}
_COL_WIDTH = 260


def _solver_node_type(solver_type: str, analysis_type: str | None) -> str:
    st = (solver_type or "").lower()
    if st == "monolithic":
        return "Solvers/Fluid dynamics/NavierStokesSolverMonolithic"
    if st in ("fractional_step", "fractionalstep"):
        return "Solvers/Fluid dynamics/NavierStokesSolverFractionalStep"
    return {
        "structural": "Solvers/Structural mechanics/StructuralMechanicsSolver",
        "thermal": "Solvers/Convection diffusion/ConvectionDiffusionSolver",
        "fluid": "Solvers/Fluid dynamics/FluidSolver",
        "potential_flow": "Solvers/Potential flow/PotentialFlowSolver",
    }.get(analysis_type or "", "Solvers/Base/Solver")


def _linear_solver_node_type(solver_type: str) -> str:
    return {
        "amgcl": "Solvers/Linear Solvers/Serial/AMGCL",
        "bicgstab": "Solvers/Linear Solvers/Serial/BICGSTAB",
        "cg": "Solvers/Linear Solvers/Serial/CG",
        "LinearSolversApplication.sparse_lu": "Solvers/Linear Solvers/Serial/SparseLU",
    }.get(solver_type, "Solvers/Linear Solvers/Serial/LinearSolver")


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.links: list[list[Any]] = []
        self._next_id = 1
        self._next_link = 1
        self._y_by_col: dict[int, int] = {}

    def add(self, node_type: str, role: str, properties: dict[str, Any],
            column_key: str, stage: str | None = None,
            extra: dict[str, Any] | None = None) -> int:
        node_id = self._next_id
        self._next_id += 1
        col = _COLUMN_X.get(column_key, 0)
        y = self._y_by_col.get(col, 100)
        self._y_by_col[col] = y + 140
        props = {"_role": role, **(extra or {}), **properties}
        if stage is not None:
            props["_stage"] = stage
        self.nodes.append({
            "id": node_id,
            "type": node_type,
            "pos": [col * _COL_WIDTH, y],
            "size": [220, 100],
            "flags": {},
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "properties": props,
        })
        return node_id

    def link(self, origin_id: int, target_id: int) -> None:
        # Best-effort visual wire (origin output 0 -> target input 0).
        self.links.append([self._next_link, origin_id, 0, target_id, 0, "*"])
        self._next_link += 1

    def emit_single(self, params: dict[str, Any], stage: str | None = None) -> None:
        solver = params.get("solver_settings", {})
        analysis_type = project_explain.detect_analysis_type(
            solver.get("solver_type", ""), params.get("analysis_stage", ""))

        # Solver core = solver_settings without the decomposed sub-blocks.
        core = {k: v for k, v in solver.items() if k not in _SOLVER_COMPONENT_KEYS}
        extra: dict[str, Any] = {}
        if "analysis_stage" in params:
            extra["_analysis_stage"] = params["analysis_stage"]
        # Record the full list of process-container keys (including empty ones)
        # so the round-trip preserves empty process lists exactly.
        if isinstance(params.get("processes"), dict):
            extra["_process_lists"] = list(params["processes"].keys())
        if isinstance(params.get("output_processes"), dict):
            extra["_output_process_lists"] = list(params["output_processes"].keys())
        solver_id = self.add(
            _solver_node_type(solver.get("solver_type", ""), analysis_type),
            "solver_settings", {"_fragment": core}, "solver_settings", stage, extra)

        if "problem_data" in params:
            pd_id = self.add("Analysis stages/Components/ProblemData", "problem_data",
                             {"_fragment": params["problem_data"]}, "problem_data", stage)
            self.link(pd_id, solver_id)

        for key in _SOLVER_COMPONENT_KEYS:
            if key in solver:
                node_type = (_linear_solver_node_type(solver[key].get("solver_type", ""))
                             if "linear_solver" in key
                             else f"Solvers/Components/{_camel(key)}")
                cid = self.add(node_type, "solver_component",
                               {"_fragment": solver[key]}, key, stage, {"_key": key})
                self.link(cid, solver_id)

        for role, container_key in (("process", "processes"),
                                    ("output_process", "output_processes")):
            container = params.get(container_key, {})
            if isinstance(container, dict):
                for list_name, blocks in container.items():
                    if not isinstance(blocks, list):
                        continue
                    for block in blocks:
                        pname = block.get("process_name") or block.get("python_module") or role
                        col = "process" if role == "process" else "output_process"
                        pid = self.add(f"Processes/{pname}", role,
                                       {"_fragment": block}, col, stage, {"_list": list_name})
                        self.link(solver_id, pid)


def _camel(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_"))


def project_to_graph(params: dict[str, Any]) -> dict[str, Any]:
    """Serialize a ProjectParameters dict to a litegraph graph.json."""
    b = _GraphBuilder()
    if "orchestrator" in params and "stages" in params:
        orch = params["orchestrator"]
        orch_id = b.add(
            "Orchestrators/" + str(orch.get("name", "")).split(".")[-1],
            "orchestrator", {"_fragment": orch}, "orchestrator")
        for name, stage in params.get("stages", {}).items():
            settings = stage.get("stage_settings", {}) if isinstance(stage, dict) else {}
            before = b._next_id
            b.emit_single(settings, stage=name)
            # Wire the stage's solver node (first added in emit_single) to orchestrator.
            b.link(orch_id, before)
    else:
        b.emit_single(params)
    return {
        "last_node_id": b._next_id - 1,
        "last_link_id": b._next_link - 1,
        "nodes": b.nodes,
        "links": b.links,
        "groups": [],
        "config": {},
        "version": 0.4,
    }


def graph_to_project(graph: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a ProjectParameters dict from a graph.json produced by
    project_to_graph. Reads the _role markers on node properties (not links)."""
    nodes = graph.get("nodes", [])
    by_role: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        props = node.get("properties", {})
        role = props.get("_role")
        if role:
            by_role.setdefault(role, []).append(props)

    def build_stage(props_list: list[dict[str, Any]]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        solver_props = next((p for p in props_list if p["_role"] == "solver_settings"), None)
        if solver_props is not None:
            solver = dict(solver_props.get("_fragment", {}))
            for comp in (p for p in props_list if p["_role"] == "solver_component"):
                solver[comp["_key"]] = comp.get("_fragment", {})
            params["solver_settings"] = solver
            if "_analysis_stage" in solver_props:
                params["analysis_stage"] = solver_props["_analysis_stage"]
        pd = next((p for p in props_list if p["_role"] == "problem_data"), None)
        if pd is not None:
            params["problem_data"] = pd.get("_fragment", {})
        for role, key, seed_key in (("process", "processes", "_process_lists"),
                                    ("output_process", "output_processes", "_output_process_lists")):
            # The container key is present iff it was present in the original
            # (recorded on the solver node), which also seeds every declared
            # list name so empty lists survive the round-trip.
            present = solver_props is not None and seed_key in solver_props
            container: dict[str, list[Any]] = {name: [] for name in solver_props[seed_key]} \
                if present else {}
            for p in (p for p in props_list if p["_role"] == role):
                container.setdefault(p.get("_list", "process_list"), []).append(p.get("_fragment", {}))
            if present or container:
                params[key] = container
        return params

    orch = by_role.get("orchestrator")
    stage_nodes = [p for role in ("solver_settings", "solver_component", "problem_data",
                                  "process", "output_process")
                   for p in by_role.get(role, [])]
    if orch:
        # Group by _stage; rebuild each stage's settings.
        stages: dict[str, Any] = {}
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for p in stage_nodes:
            by_stage.setdefault(p.get("_stage", "stage"), []).append(p)
        for stage_name, props_list in by_stage.items():
            stages[stage_name] = {"stage_settings": build_stage(props_list)}
        return {"orchestrator": orch[0].get("_fragment", {}), "stages": stages}

    return build_stage(stage_nodes)
