# Project & materials editing

Generic, stage-aware editing of `ProjectParameters.json` and `Materials.json`.
The [scaffolding](/tools/scaffolding) tools create files and append a fixed set
of boundary conditions; these tools change anything, afterwards. All are pure
Python and write atomically (temp file + rename). `//` comments, which Kratos
accepts, are tolerated when reading (and dropped on write).

**Multi-stage files** (`orchestrator` + `stages`): pass `stage="<name>"`; JSON
pointers and process lists then refer to that stage's `stage_settings`. On a
single-stage file `stage` must be omitted.

## Settings

| Tool | What it does |
| --- | --- |
| `project_get_setting(parameters_file, pointer?, stage?)` | read by JSON Pointer, e.g. `/solver_settings/time_stepping/time_step` (`""` = everything) |
| `project_set_setting(parameters_file, pointer, value, stage?, create?)` | set or add; `/processes/loads_process_list/-` appends to a list; returns the previous value |
| `project_delete_setting(parameters_file, pointer, stage?)` | remove a key or list item |
| `project_update_solver(parameters_file, settings?, linear_solver_preset?, linear_solver_settings?, time_step?, start_time?, end_time?, domain_size?, mesh_file?, materials_file?, stage?)` | deep-merge into `solver_settings`, apply a `list_linear_solver_presets` block, set times, domain size, the mesh and materials file names |

## Processes

| Tool | What it does |
| --- | --- |
| `project_list_processes(parameters_file, stage?)` | every block of `processes` and `output_processes`: section, list, index, module, model part, variable |
| `project_add_process(parameters_file, python_module, parameters?, process_list?, section?, kratos_module?, process_name?, position?, merge_defaults?, stage?)` | add **any** Kratos process; the real default settings fill omitted keys (needs the Kratos source tree); `kratos_module` is inferred |
| `project_update_process(parameters_file, parameters, process_list+index \| python_module / model_part_name / variable_name, replace?, stage?)` | deep-merge into (or replace) one block's `Parameters` |
| `project_remove_process(...same selection..., all_matches?, stage?)` | remove blocks; ambiguous matches are refused |

`project_add_process` warns about model parts that are not in the case's mesh
and, with a Kratos build, about unknown variable names. `add_boundary_condition`
also takes `stage=` and `variable=` now, plus the generic kinds `fix_scalar`,
`assign_scalar`, `fix_vector` and `assign_vector`.

## Materials

| Tool | What it does |
| --- | --- |
| `materials_get(materials_file)` | summary rows and the raw document |
| `materials_set_property(materials_file, model_part_name, properties_id?, preset?, constitutive_law?, variables?, tables?, replace?)` | add or update ONE entry (creates the file if missing); updates merge variables |
| `materials_remove_property(materials_file, model_part_name?, properties_id?)` | remove matching entries |
| `materials_check(parameters_file, applications?)` | model parts exist in the mesh, every property id used by an element has an entry, and (with a build) every constitutive law is registered |

## kratos_get_variable_info

`kratos_get_variable_info(names)` (in [Environment](/tools/environment)) checks
variable names against the live build and returns each one's type
(`DISPLACEMENT_X` resolves to its array variable).
