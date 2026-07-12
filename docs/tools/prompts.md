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
