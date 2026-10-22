# Tool reference

The server exposes **113 tools**, **21 resources** (15 worked examples) and
**8 prompts**, grouped by workflow stage:

| Category | Tools |
| --- | --- |
| [Environment & introspection](/tools/environment) | `kratos_check_installation`, `kratos_install`, `kratos_list_applications`, `kratos_list_elements`, `kratos_list_conditions`, `kratos_list_constitutive_laws`, `kratos_list_variables`, `kratos_list_solvers`, `kratos_list_processes`, `kratos_get_solver_defaults`, `kratos_get_process_defaults`, `kratos_get_variable_info` |
| [Project scaffolding](/tools/scaffolding) | `list_templates`, `create_project`, `create_multistage_project`, `create_cosim_project`, `create_fsi_project`, `create_project_parameters`, `create_materials`, `list_material_presets`, `list_linear_solver_presets`, `add_boundary_condition`, `add_output_process`, `add_remeshing_process`, `validate_project_parameters` |
| [Meshes](/tools/mesh) | `mdpa_create_structured_mesh`, `mdpa_inspect`, `mdpa_validate`, `mdpa_get_nodes`, `mesh_remesh` |
| [Mesh editing](/tools/mesh-editing) | `mdpa_get_entities`, `mdpa_select_nodes`, `mdpa_add_nodes`, `mdpa_move_nodes`, `mdpa_delete_nodes`, `mdpa_set_nodal_data`, `mdpa_add_entities`, `mdpa_delete_entities`, `mdpa_set_entity_properties`, `mdpa_create_sub_model_part`, `mdpa_edit_sub_model_part`, `mdpa_transform`, `mdpa_merge`, `mdpa_renumber`, `mdpa_extract`, `mdpa_extrude`, `mdpa_convert_cells`, `mdpa_extract_boundary`, `mdpa_set_properties`, `mdpa_quality`, `mdpa_get_data_blocks`, `mdpa_set_elemental_data`, `mdpa_set_table`, `mdpa_delete_table`, `mdpa_set_sub_model_part_data` |
| [Geometry & mesh formats](/tools/geometry) | `mdpa_create_geometry_mesh`, `mesh_import`, `mesh_export`, `mesh_generate_gmsh` (optional `gmsh` extra) |
| [Project & materials editing](/tools/project-editing) | `project_get_setting`, `project_set_setting`, `project_delete_setting`, `project_list_processes`, `project_add_process`, `project_update_process`, `project_remove_process`, `project_update_solver`, `materials_get`, `materials_set_property`, `materials_remove_property`, `materials_check` |
| [Simulation & jobs](/tools/simulation) | `run_simulation`, `validate_case`, `job_status`, `job_list`, `job_logs`, `job_cancel`, `job_rerun`, `configure_checkpoints`, `job_checkpoints`, `job_resume` |
| [Interactive sessions](/tools/sessions) | `session_start`, `session_advance`, `session_get_parameters`, `session_model_info`, `session_field_summary`, `session_inspect_node`, `session_inspect_element`, `session_set_nodal_values`, `session_exec_python` (opt-in), `session_status`, `session_list`, `session_close` |
| [Parameter & mesh studies](/tools/studies) | `study_start`, `study_status`, `study_list`, `study_cancel`, `study_resume`, `study_results` |
| [Post-processing](/tools/postprocessing) | `results_list`, `results_summary`, `results_probe`, `results_convergence`, `results_time_history`, `results_compare` |
| [Visualization](/tools/visualization) | `results_render`, `results_animate` (optional `viz` extra) |
| [Interoperability](/tools/interop) | `explain_project_parameters`, `export_case_to_flowgraph`, `import_flowgraph_to_case` |
| [Documentation lookup](/tools/docs) | `kratos_docs_structure`, `kratos_docs_read`, `kratos_docs_ask` (opt-in, DeepWiki) |

Plus [resources](/tools/resources) (templates, format guides, worked
examples, live job logs) and [prompts](/tools/prompts) (guided workflows).

## Conventions

- **Paths** are absolute; case-relative paths appear only inside Kratos input
  files and explicitly documented study filenames.
- **Errors** are returned as `{"error": "..."}` payloads rather than protocol
  errors, so assistants can read and react to them. Bridge errors include the
  tail of the worker's stdout/stderr for diagnosis.
- Tools that need a live Kratos (marked *needs Kratos* in these pages) spawn
  a subprocess; the first call after a build change takes a few seconds, then
  results are disk-cached. Everything else is pure Python and instant.

## The typical chain

```
kratos_check_installation (→ kratos_install, if missing)
  → mdpa_create_structured_mesh (or mdpa_create_geometry_mesh / mesh_import,
    then mdpa_* editing: parts, boundary conditions, quality)
  → create_project (+ add_boundary_condition / project_add_process ...)
  → validate_case
  → run_simulation → job_status / job_logs
  → results_list → results_summary / results_probe
  → results_render (inline PNG preview; results_animate for a GIF)
```

Or, to look at the model *between* steps instead of after the run:

```
session_start → session_advance(steps=N) → session_field_summary / session_inspect_node
  → (session_set_nodal_values) → session_advance … → session_close
```
