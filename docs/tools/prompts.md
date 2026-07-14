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
