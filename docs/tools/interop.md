# Interoperability

Understand an existing case, and move cases between this server and the
[Kratos FlowGraph](https://github.com/KratosMultiphysics/Flowgraph) visual node
editor. All three tools are pure JSON — no Kratos build required.

## explain_project_parameters

Parse an existing `ProjectParameters.json` and return a structured summary of
what it configures — useful for understanding a case you did not scaffold
before editing or running it.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | path to the ProjectParameters.json |

**Returns** (single-stage): `analysis_type`, `analysis_stage`, `solver`
(`solver_type`, `model_part_name`, `domain_size`), `linear_solver`,
`model_import`, `materials`, and the flattened `processes` /
`output_processes` (each with its list name, `python_module`, `process_name`
and the model parts it targets). Multi-stage (`orchestrator`/`stages`) cases
return `kind: "multi_stage"` with the `orchestrator`, `execution_list` and a
per-stage summary.

```json
// explain_project_parameters("/tmp/case/ProjectParameters.json") → (excerpt)
{
  "kind": "single_stage",
  "analysis_type": "structural",
  "solver": { "solver_type": "Static", "model_part_name": "Structure" },
  "linear_solver": { "solver_type": "LinearSolversApplication.sparse_lu" },
  "processes": [ { "list": "constraints_process_list",
                   "python_module": "assign_vector_variable_process",
                   "model_parts": ["Structure.left"] } ]
}
```

## export_case_to_flowgraph

Convert a `ProjectParameters.json` into a [FlowGraph](https://github.com/KratosMultiphysics/Flowgraph)
(litegraph) `graph.json` that opens in the visual node editor — so an
AI-built case can be inspected and hand-edited on a canvas.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | ProjectParameters.json to convert |
| `output_file` | string? | where to write the graph (also returned inline) |

Each emitted node carries a FlowGraph-compatible type and column position plus
its exact ProjectParameters fragment, so the conversion is **lossless**: it
round-trips with `import_flowgraph_to_case`. Node wiring (links) is best-effort
for visual layout.

## import_flowgraph_to_case

The inverse: convert a FlowGraph `graph.json` back into a
`ProjectParameters.json`.

| Parameter | Type | Description |
| --- | --- | --- |
| `graph_file` | string | graph.json saved by the FlowGraph editor |
| `output_file` | string? | where to write the parameters (also returned inline) |

```
export_case_to_flowgraph → edit in the FlowGraph editor → import_flowgraph_to_case
```

`import(export(params))` reproduces `params` exactly (single- and multi-stage).
