# Troubleshooting

## Kratos not found

**Symptom**: `kratos_check_installation` returns `importable: false`, tools
report "Kratos is not available".

- Set `KRATOS_ROOT` to a checkout that contains `bin/Release/KratosMultiphysics/`.
- If your build lives elsewhere, set `KRATOS_PYTHONPATH` (dir containing
  `KratosMultiphysics/`) and `KRATOS_LIBS` (its `libs/`).
- Remember the variable must reach the *server* process: pass it via the MCP
  client's `env` block, not just your shell.

## `libmkl_rt.so.2: cannot open shared object file`

The LinearSolversApplication was built against Intel MKL. The server
auto-detects MKL at `/opt/intel/oneapi/mkl/latest/lib`; if yours is
elsewhere:

```bash
export KRATOS_EXTRA_LIBS=/path/to/mkl/lib
```

## Simulation runs but displacements are exactly zero

Almost always: the mesh uses **geometric** conditions
(`LineCondition2D2N`, `SurfaceCondition3D4N`) where **load** conditions are
needed (`LineLoadCondition2D2N`, `SurfaceLoadCondition3D4N`). Geometric
conditions silently ignore `LINE_LOAD`/`SURFACE_LOAD`. The solver even prints
a hint:

```
ResidualBasedBlockBuilderAndSolver: ATTENTION! setting the RHS to zero!
```

Regenerate the mesh with the right `condition_name` (the tool defaults are
correct for structural cases), or apply a nodal/volume load instead.

## `Error: Element ... is not registered in Kratos`

- The application registering that element is not compiled into your build —
  check with `kratos_list_applications`, and `kratos_list_elements` shows a
  `compiled` flag per element.
- Watch the exact spelling and node count (`SmallDisplacementElement2D4N`
  exists; `LaplacianElement2D4N` does not — the Laplacian family only has
  simplex variants).

## `Found a condition without any corresponding element` (thermal/fluid)

The convection-diffusion solver's mesh checks require **simplex** meshes.
Generate thermal meshes with `triangles: true` and `element_name:
'Element2D3N'`. Every condition must also be a face of some element —
generated meshes guarantee this.

## Stationary thermal analysis behaves like one transient step

`solver_type: stationary` still uses the transient element by default. A true
steady state needs:

```json
"element_replace_settings": { "element_name": "LaplacianElement", "condition_name": "ThermalFace" }
```

The `thermal_stationary` template includes this.

## Job stuck in `running` / server restarted mid-run

Jobs are detached processes; a server restart does not kill them.
`job_status` re-evaluates state from pid liveness and the log tail. If a pid
was reused and the state looks wrong, `job_cancel` forces the record to
`cancelled`.

## Tool calls are slow the first time

The first bridge call after a (re)build spawns a Kratos interpreter (~1–3 s)
and repopulates the disk cache under `~/.kratos-mcp/cache/`. Later calls are
instant. Deleting that directory is always safe.

## `model_part_name '...' does not match any SubModelPart`

`validate_case` compares every `model_part_name` in your processes and
materials against the mesh. Names are dotted paths **rooted at the solver's
`model_part_name`** — for a solver rooted at `Structure` and a mesh part
`left`, write `Structure.left`. Use `mdpa_inspect` to list the parts a mesh
actually contains.

## Debugging a failed run

1. `job_status(job_id)` — how far did it get (`progress.current_step`)?
2. `job_logs(job_id, tail=200)` — the Python traceback or Kratos error is at
   the end.
3. `results_convergence(job_id)` — nonlinear steps that hit `max_iteration`.
4. `validate_case(case_dir)` — re-check the configuration.

The `debug_failed_simulation` prompt packages this workflow.
