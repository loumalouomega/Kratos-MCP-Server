"""Interoperability tools: explain an existing ProjectParameters.json and
convert cases to/from a Flowgraph (litegraph) node graph.

All pure-JSON, in the server process -- no Kratos import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import flowgraph, project_explain


def _load_json(path_str: str) -> tuple[dict[str, Any] | None, Path, dict[str, Any] | None]:
    """Load and parse a JSON file, returning (data, resolved_path, error)."""
    path = Path(path_str).expanduser().resolve()
    if not path.is_file():
        return None, path, {"error": f"{path} does not exist"}
    try:
        return json.loads(path.read_text()), path, None
    except json.JSONDecodeError as exc:
        return None, path, {"error": f"Invalid JSON in {path.name}: {exc}"}


def register(mcp) -> None:

    @mcp.tool()
    def explain_project_parameters(parameters_file: str) -> dict[str, Any]:
        """Parse an existing ProjectParameters.json and return a structured
        summary of what it configures: analysis type, solver + linear solver,
        mesh and material import, and the flattened boundary-condition / load /
        output process lists (with the model parts each targets). Multi-stage
        (orchestrator/stages) cases are summarized per stage. Use this to
        understand a case you did not scaffold before editing or running it."""
        data, _path, err = _load_json(parameters_file)
        if err is not None:
            return err
        return project_explain.explain(data)

    @mcp.tool()
    def export_case_to_flowgraph(
        parameters_file: str, output_file: str | None = None
    ) -> dict[str, Any]:
        """Convert a ProjectParameters.json into a Flowgraph (litegraph)
        graph.json that can be opened in the Kratos FlowGraph visual node
        editor. Returns the graph; when output_file is given it is also
        written there. Round-trips with import_flowgraph_to_case."""
        data, _path, err = _load_json(parameters_file)
        if err is not None:
            return err
        graph = flowgraph.project_to_graph(data)
        out: dict[str, Any] = {"graph": graph}
        if output_file:
            path = Path(output_file).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(graph, indent=2))
            out["written_to"] = str(path)
        return out

    @mcp.tool()
    def import_flowgraph_to_case(
        graph_file: str, output_file: str | None = None
    ) -> dict[str, Any]:
        """Convert a Flowgraph (litegraph) graph.json -- as saved by the
        Kratos FlowGraph editor -- back into a ProjectParameters.json.
        Returns the parameters; when output_file is given it is also written
        there. Round-trips with export_case_to_flowgraph."""
        data, _path, err = _load_json(graph_file)
        if err is not None:
            return err
        try:
            params = flowgraph.graph_to_project(data)
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"Could not reconstruct ProjectParameters from graph: {exc}"}
        out: dict[str, Any] = {"parameters": params}
        if output_file:
            path = Path(output_file).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(params, indent=4))
            out["written_to"] = str(path)
        return out
