# Prompts

MCP prompts are parameterised, ready-made instructions that walk an
assistant through a multi-step workflow with the right tools in the right
order.

## setup_structural_analysis

| Argument | Default |
| --- | --- |
| `description` | "a cantilever plate fixed on the left with a downward load on the right" |

Guides the full structural chain: check installation → pick template →
generate mesh → scaffold project → add loads → validate → run → post-process
with plausibility checks.

## setup_thermal_analysis

| Argument | Default |
| --- | --- |
| `description` | "a plate with a hot left edge and a cold right edge" |

Same shape for heat conduction, including the thermal-specific mesh rules
(simplex elements, `ThermalFace2D2N` conditions).

## setup_fluid_analysis

| Argument | Default |
| --- | --- |
| `description` | "flow past a body with an inlet velocity, an outlet, and no-slip walls" |

Same shape for incompressible flow (`fluid_transient`, Monolithic/VMS):
check installation → get a mesh (structured or an externally-authored one
for curved boundaries) → scaffold project, overriding submodelpart
placeholders to match the mesh → add no-slip walls → validate → run →
post-process, including summing `REACTION` for a drag/lift estimate if
`compute_reactions` was enabled.

## debug_failed_simulation

| Argument | |
| --- | --- |
| `job_id` | the failed job |

A diagnosis checklist: status/progress → log tail → common root causes
(unregistered elements, mesh problems, missing model parts, non-convergence)
→ fix and re-run.

## postprocess_results

| Argument | |
| --- | --- |
| `case_dir` | the finished case |

Structured reporting: discover outputs → summarise fields → probe points of
interest → convergence review → headline numbers with plausibility checks.

## interactive_session

| Argument | |
| --- | --- |
| `case_dir` | the case to drive |
| `goal` | what to find out while stepping (optional) |

Walks through the [session](/tools/sessions) workflow: validate → `session_start`
(isolated) → check the live parameters → loop of `session_advance` /
`session_field_summary` / `session_inspect_node` with convergence and
plausibility checks → optional `session_set_nodal_values` → `session_close` →
report.

## build_mesh_from_geometry

| Argument | Default |
| --- | --- |
| `description` | — |
| `output_dir` | — |
| `element_type` | `SmallDisplacementElement2D3N` |

Chooses a generator (structured, parametric, gmsh, import), shapes it
(extrude, convert cells, transform + merge), names the boundaries with
selectors, checks quality and turns the mesh into a runnable case.

## edit_existing_case

| Argument | Default |
| --- | --- |
| `case_dir` | — |
| `change` | — |

Modifies a case with the narrowest tool (`project_*`, `materials_*`, `mdpa_*`),
re-checks model-part references and quality, then validates and re-runs.

