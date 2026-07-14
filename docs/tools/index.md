# Tool reference

The server exposes **40 tools**, **15 resources** (10 worked examples) and
**5 prompts**, grouped by workflow stage:

| Category | Tools |
| --- | --- |
| [Environment & introspection](/tools/environment) | `kratos_check_installation`, `kratos_install`, `kratos_list_applications`, `kratos_list_elements`, `kratos_list_conditions`, `kratos_list_constitutive_laws`, `kratos_list_variables`, `kratos_list_solvers`, `kratos_list_processes`, `kratos_get_solver_defaults`, `kratos_get_process_defaults` |
| [Project scaffolding](/tools/scaffolding) | `list_templates`, `create_project`, `create_multistage_project`, `create_project_parameters`, `create_materials`, `list_material_presets`, `list_linear_solver_presets`, `add_boundary_condition`, `add_output_process`, `validate_project_parameters` |
| [Meshes](/tools/mesh) | `mdpa_create_structured_mesh`, `mdpa_inspect`, `mdpa_validate`, `mdpa_get_nodes` |
| [Simulation & jobs](/tools/simulation) | `run_simulation`, `validate_case`, `job_status`, `job_list`, `job_logs`, `job_cancel` |
| [Post-processing](/tools/postprocessing) | `results_list`, `results_summary`, `results_probe`, `results_convergence` |
| [Visualization](/tools/visualization) | `results_render`, `results_animate` (optional `viz` extra) |
| [Interoperability](/tools/interop) | `explain_project_parameters`, `export_case_to_flowgraph`, `import_flowgraph_to_case` |

Plus [resources](/tools/resources) (templates, format guides, worked
examples, live job logs) and [prompts](/tools/prompts) (guided workflows).

## Conventions

- **Paths** are absolute; case-relative paths appear only inside Kratos input
  files.
- **Errors** are returned as `{"error": "..."}` payloads rather than protocol
  errors, so assistants can read and react to them. Bridge errors include the
  tail of the worker's stdout/stderr for diagnosis.
- Tools that need a live Kratos (marked *needs Kratos* in these pages) spawn
  a subprocess; the first call after a build change takes a few seconds, then
  results are disk-cached. Everything else is pure Python and instant.

## The typical chain

```
kratos_check_installation (→ kratos_install, if missing)
  → mdpa_create_structured_mesh
  → create_project (+ add_boundary_condition ...)
  → validate_case
  → run_simulation → job_status / job_logs
  → results_list → results_summary / results_probe
  → results_render (inline PNG preview; results_animate for a GIF)
```
