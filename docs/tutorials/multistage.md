# Tutorial: multi-stage load steps

Some workflows are more than one analysis: a continuation run, a coupled
sequence, or — here — **load stepping**, where a structure is loaded in stages.
Kratos drives these with a `SequentialOrchestrator`; this server composes one
with `create_multistage_project`. A cantilever is solved in two stages that
share a mesh: stage 1 applies 1 MN/m, stage 2 applies 2 MN/m. Every number below
comes from a real run against Kratos 10.4.

The `kratos://examples/multistage-load-steps` resource is this exact case, and
`notebooks/multistage.ipynb` walks through it live — including
`explain_project_parameters` and the Flowgraph round-trip.

## 1. Compose the orchestrated case

`create_multistage_project` builds the whole `orchestrator` + `stages` +
`execution_list` structure from ordinary single-stage templates:

```json
create_multistage_project({ "directory": "/tmp/ms", "name": "cantilever_ms",
  "stages": [
    { "name": "load_step_1", "template": "structural_static" },
    { "name": "load_step_2", "template": "structural_static" }
]})
→ { "execution_list": ["load_step_1", "load_step_2"],
    "created": [".../ProjectParameters.json", ".../Materials.json"] }
```

The two stages share one mesh: the second reuses the first's model part
(`model_import_settings.input_type: "use_input_model_part"`) instead of
re-importing — which is how state flows from one stage to the next. A later stage
with a *different* `model_part_name` would import its own mesh instead.

## 2. Add the load steps and the mesh

Each stage's load lives inside its `stage_settings.processes` — so the load is
set per stage (an increasing line load on the right edge), and the shared mesh is
generated once:

```json
mdpa_create_structured_mesh({ "path": "/tmp/ms/mesh.mdpa",
  "kind": "rectangle", "size": [1.0, 0.2], "divisions": [10, 4] })
→ { "num_nodes": 55, "num_elements": 40 }
```

## 3. Validate and run

`validate_case` recognises the multi-stage structure and checks each stage;
`run_simulation` drives the whole thing through the orchestrator — no special
flag, the same tool as any case:

```json
validate_case({ "case_dir": "/tmp/ms" }) → { "valid": true }
run_simulation({ "case_dir": "/tmp/ms", "wait_seconds": 60 })
→ { "state": "succeeded" }
```

The job log shows `Analysis -START-`/`Analysis -END-` **twice**: the orchestrator
ran both stages in `execution_list` order.

## 4. The result: load-stepping

Reading each stage's tip deflection:

```
stage load_step_1  (1 MN/m):  tip uy = -4.00e-04 m
stage load_step_2  (2 MN/m):  tip uy = -8.00e-04 m
```

The tip deflection **doubles** with the doubled load — exactly, because each
stage is a linear static solve.

## 5. Explain and export

`explain_project_parameters` returns a structured summary of any case; on a
multi-stage file it lists the orchestrator, the execution list, and each stage:

```json
explain_project_parameters({ "parameters_file": "/tmp/ms/ProjectParameters.json" })
→ { "kind": "multi_stage", "orchestrator": "SequentialOrchestrator",
    "execution_list": ["load_step_1", "load_step_2"], "stages": [ ... ] }
```

And the case round-trips through the [Kratos FlowGraph](https://github.com/KratosMultiphysics/Flowgraph)
visual editor losslessly:

```json
export_case_to_flowgraph({ "parameters_file": ".../ProjectParameters.json",
                           "output_file": ".../graph.json" })
import_flowgraph_to_case({ "graph_file": ".../graph.json" })
// import(export(params)) reproduces params exactly
```

## Variations

- **More stages** — add entries to `stages`; they run in the order given.
- **Coupled physics** — give two stages the same `model_part_name` and different
  physics (e.g. a thermal stage that writes TEMPERATURE, then a structural stage
  that reads it) to chain fields across a shared mesh.
- **Checkpoints** — `stage_checkpoints: true` writes per-stage restart data.
