# Project scaffolding

Create and edit the Kratos input files: `ProjectParameters.json` (solver +
processes), `Materials.json` (constitutive laws + properties) and complete
case directories.

## list_templates

List available case templates with descriptions, required applications and
every placeholder with its default.

**Templates**: `structural_static`, `structural_dynamic`, `structural_modal`,
`thermal_transient`, `thermal_stationary`, `fluid_transient`,
`fluid_fractional_step`, `potential_flow`.

::: tip
`potential_flow` requires `CompressiblePotentialFlowApplication` (not always
compiled). `fluid_fractional_step` uses the cheaper pressure-splitting scheme
(good for large meshes) instead of the monolithic solver.
:::

## create_project

Scaffold a complete case directory from a template.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `template` | string | template name from `list_templates` |
| `name` | string | problem name (default `case`) |
| `overrides` | object? | placeholder overrides, e.g. `{"end_time": 2.0, "young_modulus": 7e10}` |
| `create_demo_mesh` | bool | also write a small rectangle mesh wired to the defaults, so the case runs out of the box (default false) |

**Returns**: created file list, `required_applications`, and `next_steps`
(create mesh → validate → run).

```json
// create_project("/tmp/my-case", "structural_static", "cantilever",
//                {"fix_model_part": "Structure.left"})
{
  "case_dir": "/tmp/my-case",
  "created": ["/tmp/my-case/ProjectParameters.json", "/tmp/my-case/Materials.json"],
  "next_steps": ["Create the mesh at /tmp/my-case/mesh.mdpa ...", "..."]
}
```

## create_multistage_project

Scaffold a **multi-stage (orchestrated) case** that chains several analyses in
sequence, run with Kratos' native `SequentialOrchestrator`. Use it for a
continuation run (e.g. two load steps) or a coupled workflow where a later
physics reads fields the earlier one wrote on the same mesh.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `stages` | array | one entry per stage: `{"name": "<id>", "template": "<template>", "overrides": {...}}` |
| `name` | string | base problem name (default `case`) |
| `stage_checkpoints` | bool | write per-stage checkpoints (default false) |
| `create_demo_mesh` | bool | also write a small rectangle mesh (from the first stage's template) so the case runs out of the box (default false) |

**Mesh sharing**: the first stage imports its mesh; a later stage whose solver
`model_part_name` matches an earlier stage's **reuses** that already-populated
model part (`input_type: "use_input_model_part"`) — this is how state flows
between stages. A later stage with a distinct `model_part_name` imports its own
mesh instead.

The composed file uses the `orchestrator` + `stages` + `execution_list`
structure; `run_simulation` and `validate_case` handle it transparently.

```json
// create_multistage_project("/tmp/ms", [
//   {"name": "load_1", "template": "structural_static", "overrides": {"end_time": 1.0}},
//   {"name": "load_2", "template": "structural_static", "overrides": {"end_time": 2.0}}])
{ "case_dir": "/tmp/ms", "execution_list": ["load_1", "load_2"],
  "created": ["/tmp/ms/ProjectParameters.json", "/tmp/ms/Materials.json"], "next_steps": ["..."] }
```

## create_cosim_project

Scaffold a **CoSimulation (coupled-solver) case**: several solvers that
exchange interface data inside every time step, driven by Kratos'
`CoSimulationAnalysis` (needs `KratosCoSimulationApplication`). `run_simulation`
detects the case (`solver_settings.type` starting `coupled_solvers.`, with a
`coupling_sequence` and `solvers`) and launches it through that entry point —
`CoSimulationAnalysis(cosim_settings)` has no `(model, parameters)`
constructor, so it cannot go through the usual dispatch.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `solvers` | array | one entry per solver: `{"name", "type", "data", "parameters"?, "input_file"?}` (below) |
| `couplings` | array | `{"from_solver", "from_data", "to_solver", "to_data", "operator"?="copy", "options"?=[]}` |
| `order` | array | coupling sequence; defaults to the order of `solvers` |
| `coupled_solver` | string | `gauss_seidel_weak` (default, one pass per step), `jacobi_weak`, or a strong variant (`gauss_seidel_strong`, `jacobi_strong`, `block_strong`) that iterates to convergence |
| `start_time`, `end_time` | number | `problem_data` times |
| `num_coupling_iterations` | int | iteration cap of the strong variants (default 10) |
| `convergence_criteria` | array | required by strong variants, e.g. `{"type": "relative_norm_initial_residual", "solver": s, "data_name": d, "abs_tolerance": 1e-10, "rel_tolerance": 1e-8}` |
| `convergence_accelerators` | array | e.g. `{"type": "aitken", "solver": s, "data_name": d}` |
| `data_transfer_operators` | object | extra operators; `copy` is always defined (`kratos_mapping` etc. for non-matching interfaces) |

A solver entry's `type` is a CoSimulation wrapper module
(`solver_wrappers.kratos.structural_mechanics_wrapper`, …) or one of the
shorthands `sdof` / `sdof_static` — the Python-only single-degree-of-freedom
solvers, which need no mesh and are the cheapest way to try a coupling
(`data` entries get `"location": "model_part"` automatically). Pass
`parameters` to have the solver's own parameter file written to `<name>.json`,
or `input_file` to reference one already in the directory.

A coupling whose producer comes **first** in `order` is written as an
*output* of the producer (exchanged right after it solves); otherwise as an
*input* of the consumer (exchanged right before it solves, carrying the
previous iteration's value) — the usual Gauss–Seidel semantics. The result is
statically validated and returned under `validation`. For Kratos-wrapped
solvers the validation reads each sub-solver's mesh and checks that every
`data.*.model_part_name` is the solver's root model part or one of its
SubModelParts, and warns when the two ends of a `kratos_mapping` transfer have
different `dimension`s.

Not supported for CoSimulation cases (clear errors instead): `mpi_ranks`,
studies, checkpoints/resume, interactive sessions and Flowgraph export. VTK
output of Kratos-wrapped sub-solvers is recorded in `result-index.json` (one
series per sub-solver model part), so `results_time_history` works on it —
select a `series`.

See the worked examples `kratos://examples/coupled-sdof` (Python-only) and
`kratos://examples/coupled-blocks` (two meshes, non-matching interfaces).

## create_fsi_project

Scaffold a **partitioned fluid-structure interaction (FSI) case** for Kratos'
`FSIApplication`: a 2D channel `[0, length] x [0, fluid_height]` whose floor is an
elastic block `[0, length] x [-wall_thickness, 0]`. The fluid (monolithic
Navier–Stokes; inlet velocity ramped over `ramp_time`, outlet pressure 0, no-slip
top) loads the wall through the shared `Interface`; the deformed wall moves the
fluid mesh (ALE, `mesh_solver_structural_similarity`). Unlike CoSimulation,
`FsiAnalysis` keeps the usual `(model, parameters)` constructor, so
`run_simulation` needs no special entry point: it detects `solver_type:
"partitioned"` and runs `FsiAnalysis`. Needs `FSIApplication`,
`FluidDynamicsApplication`, `StructuralMechanicsApplication`,
`MeshMovingApplication` and `MappingApplication`.

| Parameter | Type | Description |
| --- | --- | --- |
| `directory` | string | case directory (created if missing) |
| `length`, `fluid_height`, `wall_thickness` | number | geometry (2.0, 1.0, 0.4) |
| `fluid_divisions`, `wall_divisions` | `[nx, ny]` | structured divisions of the triangle fluid mesh (30, 12) and the quad wall mesh (20, 4) — **deliberately different interface discretizations** (31 vs 21 nodes), so the mapper does real work |
| `inlet_velocity`, `ramp_time` | number | peak parabolic inlet speed and the cosine ramp that gets there (1.0, 1.0) |
| `end_time`, `time_step` | number | 2.0, 0.1 |
| `fluid_density`, `fluid_viscosity` | number | 1.0, 1.0 (a Stokes-like regime) |
| `young_modulus`, `poisson_ratio` | number | wall material, plane strain (200, 0.3) |
| `dynamic_structure`, `wall_density` | bool, number | `False`: static wall (quasi-steady structure, default); `True`: Bossak dynamic wall with the given density |
| `accelerator` | string | `MVQN_recursive` (default), `MVQN`, `Relaxation` (Aitken), `constant_relaxation`, `IBQN_MVQN`, `MVQN_randomized_SVD`, `IBQN_MVQN_randomized_SVD` |
| `coupling_tolerance`, `max_coupling_iterations` | number, int | `nl_tol` (1e-6) and `nl_max_it` (25) of the Dirichlet–Neumann iteration |
| `output_interval` | int | VTK output every N steps (5) |

It writes `ProjectParameters.json`, `fluid.mdpa`, `structure.mdpa`,
`MaterialsFluid.json` and `MaterialsStructure.json`, and returns the statically
validated case under `validation`. With the defaults every step converges in 3–7
coupling iterations and the floor settles at a deflection of about −2.05e-2 (2 %
of the channel height) in a few seconds.

**Choosing the wall stiffness.** Dirichlet–Neumann iteration diverges when the wall
is too soft for the fluid: the dimensionless group
`wall_thickness * viscosity * length^2 / (E * fluid_height^3 * time_step)` must
stay well below 1, and a high Reynolds number adds fluid inertia that also
destabilizes a soft wall. Typical symptom: `Fluid solver did not converge`
warnings, `|res|/sqrt(nDOFS) = nan` in the log and `FSI non-linear maximum
iterations N reached` — the run still exits with status 0, so check
`results_convergence` (a non-converged step shows `converged: false`).

Not supported for FSI cases (clear errors instead): `mpi_ranks`, studies,
checkpoints/resume (`FsiAnalysis` has no restart), interactive sessions and
Flowgraph export. See the worked example `kratos://examples/fsi-flexible-flap`
(the Mok benchmark, reused from Kratos' own FSI tests) and the
[FSI tutorial](/tutorials/fsi).

## add_remeshing_process

Add an MMG remeshing process (`mmg_process` from the MeshingApplication) to a
ProjectParameters.json. The mesh is remeshed **once at setup**, before the solver
initializes (`initial_remeshing: true`), then the case runs on the adapted mesh.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | the ProjectParameters.json to edit |
| `strategy` | string | `hessian` (second derivatives of the scalar `variable`) or `levelset` (zero of the signed-distance `variable`; `<variable>_GRADIENT` must exist too) |
| `variable` | string | nodal scalar variable driving the metric, e.g. `DISTANCE` |
| `model_part` | string? | root model part (default `solver_settings.model_part_name`) |
| `min_size`, `max_size` | number? | forced element size bounds; without them MMG derives sizes from the current mesh (`automatic_remesh`) |
| `freeze_sub_model_parts` | string[]? | sub model parts whose nodes are blocked |

The field driving the metric must already be on the nodes when the process
initializes: put it in the mesh as a `Begin NodalData DISTANCE` block and list
`DISTANCE`/`DISTANCE_GRADIENT` in the solver's `auxiliary_variables_list` so the
variables exist (processes that assign values run later, in
`ExecuteBeforeSolutionLoop`). Remeshing **inside** the solution loop is not offered: a
standard solver keeps its strategy bound to the old mesh and the run aborts with
SIGSEGV after the first remesh (verified with the thermal solver);
`validate_case` warns about such hand-written blocks.

Remeshing renumbers nodes and elements, so cases with an `mmg_process` are
rejected by studies, checkpoints/resume, `mpi_ranks`, interactive sessions;
`validate_case(deep=true)` checks that MMG is available. Use
[`mesh_remesh`](/tools/mesh#mesh-remesh) for an inspectable, offline refinement.
Worked example: `kratos://examples/adaptive-remeshing` (`setup_remesh/`: 231 nodes →
1608 nodes around a disk).

## create_project_parameters

Render only a ProjectParameters.json (returned, and optionally written to
`output_file`). Same `template`/`overrides` semantics as `create_project`.

## create_materials

Write a Materials.json from a list of material specs.

| Parameter | Type | Description |
| --- | --- | --- |
| `output_file` | string | where to write |
| `materials` | array | one entry per model part (below) |

Each entry: `model_part_name` (e.g. `Structure.domain`); then either a
`preset` (a name from `list_material_presets`, which fills `constitutive_law`
and default `variables`) **or** an explicit `constitutive_law` (thermal
problems have none) plus `variables` (e.g. `{"YOUNG_MODULUS": 2.1e11,
"POISSON_RATIO": 0.3}`); optional `properties_id`. With a preset, any
`variables` you pass override the preset's defaults.

## list_material_presets

List the curated material presets (constitutive law + default variables) usable
as `preset` in `create_materials`: linear elastic (3D / plane strain / plane
stress), small- and finite-strain Von Mises plasticity, isotropic damage, and
Newtonian fluids. Cross-check the law names with `kratos_list_constitutive_laws`
for your compiled build. These are seeded from the sibling
[Flowgraph](https://github.com/KratosMultiphysics/Flowgraph) material node library.

## list_linear_solver_presets

List curated `linear_solver_settings` presets — drop-in blocks for
`solver_settings.linear_solver_settings`. Serial: `sparse_lu`, `skyline_lu`,
`amgcl`, `cg`, `bicgstab`. MPI/Trilinos: `amgcl_mpi`, `amesos`, `aztec`, `ml`.

## add_boundary_condition

Insert a boundary condition or load process block into an existing
ProjectParameters.json.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | the file to edit |
| `kind` | string | see table below |
| `model_part` | string | dotted target, e.g. `Structure.right` |
| `value` | number \| number[3]? | for fix/prescribe kinds |
| `modulus`, `direction` | number, number[3] | for directional loads |
| `interval` | [start, end]? | default `[0.0, "End"]` |
| `process_list` | string? | override the target list |

| kind | variable | typical use |
| --- | --- | --- |
| `fix_displacement` / `prescribed_displacement` | DISPLACEMENT | supports / imposed motion |
| `fix_velocity` / `inlet_velocity` | VELOCITY | fluid walls / inlets |
| `outlet_pressure` | PRESSURE | fluid outlets |
| `fix_temperature` | TEMPERATURE | thermal Dirichlet |
| `point_load` / `line_load` / `surface_load` | POINT_LOAD / LINE_LOAD / SURFACE_LOAD | directional loads on conditions (need `modulus` + `direction`) |
| `pressure_load` | POSITIVE_FACE_PRESSURE | pressure on faces |
| `surface_heat_flux` | FACE_HEAT_FLUX | thermal Neumann on conditions |
| `volume_heat_source` | HEAT_FLUX | volumetric heating |
| `self_weight` | VOLUME_ACCELERATION | gravity (`modulus` defaults to 9.81) |

```json
// add_boundary_condition(file, "line_load", "Structure.right",
//                        modulus=1e6, direction=[0, -1, 0])
{ "process_list": "loads_process_list", "added": { "python_module": "assign_vector_by_direction_to_condition_process", "...": "..." } }
```

::: warning
Condition-based loads (`point/line/surface_load`, `pressure_load`,
`surface_heat_flux`) need actual load-bearing conditions in the target
region — see [the MDPA guide](/guide/mdpa-format#naming-conventions).
:::

::: tip
When the Kratos source tree is available, the inserted block's `Parameters`
are auto-completed with the process' real `default_settings` (via
[`kratos_get_process_defaults`](/tools/environment#kratos-get-process-defaults))
for any key you did not set, so blocks stay correct even as Kratos evolves.
Without a source tree it falls back to the built-in defaults.
:::

## add_output_process

Add an output process to a ProjectParameters.json.

| Parameter | Type | Description |
| --- | --- | --- |
| `parameters_file` | string | file to edit |
| `format` | string | `vtk` (ParaView), `json` (variable time series), `point` (probe a coordinate) |
| `variables` | string[]? | variables to write |
| `model_part` | string? | defaults to the solver root |
| `output_path` | string | for `vtk` (default `vtk_output`) |
| `output_file` | string? | for `json`/`point` |
| `position` | number[3]? | for `point` |

## validate_project_parameters

*deep mode needs Kratos*

Validate a ProjectParameters.json without running anything:

1. JSON syntax and required top-level keys,
2. referenced mesh/materials files exist and parse,
3. every `model_part_name` in processes/materials matches a mesh
   submodelpart,
4. (`deep: true`, default) `solver_settings` validated against the solver's
   `GetDefaultParameters()` inside a Kratos worker.

Multi-stage (`orchestrator`/`stages`) cases are recognised automatically and
validated per stage (structure, execution list, per-stage mesh/material refs).
Deep mode validates every executed stage against its solver defaults and
prefixes failures with the stage settings path. If an optional application is
unavailable, validation remains static and reports a warning.

CoSimulation cases are recognised too and checked before launch: every name in
`coupling_sequence` must be a declared solver, every `data` /
`from_solver_data` / `to_solver_data` must be declared in the owning solver's
`data`, every `data_transfer_operator` (and before/after operation) must be
defined, strong coupled solvers need `convergence_criteria` whose `solver` /
`data_name` exist, each sub-solver's input file must exist (SDoF input files
are key-checked; Kratos-wrapped sub-solvers get the regular single-stage
validation, including the deep Kratos-side check). Kratos reports these
problems only mid-run, if at all. The interface `model_part_name`s of
Kratos-wrapped solvers are cross-checked against their meshes.

Partitioned FSI cases (`solver_type: "partitioned"`) are recognised and checked
before launch: the nested `fluid_solver_settings`, `structure_solver_settings`,
`mesh_solver_settings` (not for `partitioned_embedded`) and `coupling_settings`
must be present, the fluid and structure must use different root model parts, the
mesh solver must act on the fluid model part, all `domain_size`s must agree, both
meshes and both materials files must exist and parse, and every model part named
in the solver lists (`volume_model_part_name`, `skin_parts`, `no_skin_parts`,
`problem_domain_sub_model_part_list`, …), in the process lists and in the
materials must exist in the matching mesh. The coupling interface is checked
hardest: one mapper (single-faced) or two (`Positive`/`Negative`) sharing one
structure interface; each interface must be a **full** model part name
(`FluidModelPart.Interface` — a bare name makes Kratos 10.x fail with "not found
as root-ModelPart"), exist in its mesh, be non-empty and be listed in
`fluid_interfaces_list` / `structure_interfaces_list`. A warning is raised when the
two interfaces do not occupy the same region at t = 0. Deep mode also checks that
the five required applications are compiled in.

**Returns**: `{valid, issues: [...], warnings: [...], deep_validated}`.
