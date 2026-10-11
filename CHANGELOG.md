# Changelog

Notable changes to Kratos MCP Server. Historical entries are reconstructed
from repository tags and diffs; dates are the tagged commits' dates.

## Unreleased

### Added

- **Mesh building and editing (37 new tools)**. `mdpa_*` editing tools
  (`mdpa_get_entities`, `mdpa_select_nodes`, `mdpa_add/move/delete_nodes`,
  `mdpa_add/delete_entities`, `mdpa_set_entity_properties`,
  `mdpa_create/edit_sub_model_part`, `mdpa_transform`, `mdpa_merge`,
  `mdpa_renumber`, `mdpa_extract`, `mdpa_extrude`, `mdpa_convert_cells`,
  `mdpa_extract_boundary`, `mdpa_set_properties`, `mdpa_set_nodal_data`,
  `mdpa_quality`) with geometric node selectors; in-place or `output=` writes, and
  in-place edits are refused when the file holds blocks the model drops.
  `mdpa.py` now reads/writes `Begin Geometries` and `SubModelPartGeometries`, and
  `validate()` also checks connectivity length against the `...2D3N` name suffix and
  duplicate ids. New pure-Python modules `mesh_edit.py`, `geometry.py`, `mesh_io.py`.
- **Geometry and interchange**: `mdpa_create_geometry_mesh` (annulus, disc,
  cylinder, sphere, polygon), `mesh_import`/`mesh_export` (meshio) and
  `mesh_generate_gmsh` (polygon with holes, box/cylinder/sphere solids, CAD files)
  behind the new optional `gmsh` extra; gmsh runs in the `gmsh_worker.py`
  subprocess, never in the server.
- **Project and materials editing**: `project_get/set/delete_setting` (JSON Pointer,
  multi-stage aware), `project_list/add/update/remove_process` (any Kratos process,
  defaults merged), `project_update_solver`, `materials_get/set_property/
  remove_property/check`, `kratos_get_variable_info`. `add_boundary_condition` gained
  `stage`, `variable` and the generic kinds `fix_/assign_scalar` and `fix_/assign_vector`.
- Resource `kratos://docs/mesh-editing`; prompts `build_mesh_from_geometry` and
  `edit_existing_case`.
- Tests: `test_mesh_edit.py`, `test_geometry.py`, `test_mesh_io.py`,
  `test_edit_tools.py` (Kratos-free) and kratos-marked `test_edit_integration.py`
  (cantilever from a plain mesh, Lame thick cylinder within 3 %, extruded hex/tet beams).
- Verified: in Kratos 10.4 a positive `POSITIVE_FACE_PRESSURE` on conditions with the
  domain on their left acts as tension; compressive pressure needs a negative value.

- **CoSimulation (coupled-solver) cases**: `run_simulation` now detects a
  coupled solver (`solver_settings.type` `coupled_solvers.*` with a
  `coupling_sequence` and `solvers`) and drives it through
  `CoSimulationAnalysis(cosim_settings)`, whose constructor differs from the
  `(model, parameters)` AnalysisStage entry point. New `create_cosim_project`
  tool (Python-only `sdof`/`sdof_static` shorthands or any CoSimulation
  wrapper; weak and strong Gauss-Seidel/Jacobi coupling with convergence
  criteria and accelerators). `validate_case` / `validate_project_parameters`
  statically diagnose unknown solvers, data names, transfer operators and
  sub-solver input files before launch; Kratos-wrapped sub-solvers get the
  regular single-stage validation. `explain_project_parameters` returns
  `kind: "cosimulation"`; `results_convergence`/`job_status` understand the
  `time=... | step=N` banner and coupling-iteration lines. New pure-Python
  module `cosim.py`.
- `kratos://examples/coupled-sdof`: two SDoF oscillators coupled as a
  mass-spring chain (strong Gauss-Seidel + Aitken), verified interface data
  exchange; tutorial `docs/tutorials/cosimulation.md`.
- Tests: `tests/test_cosim.py` (Kratos-free) and kratos-marked example tests.
- **Interactive sessions**: a second execution model next to batch jobs. A
  long-lived `session_worker` process keeps a live Kratos `Model` and
  initialized `AnalysisStage` in memory; `sessions.py` drives it over JSON lines.
  Twelve new tools — `session_start`, `session_advance` (steps or `until_time`,
  mirroring `RunSolutionLoop`), `session_get_parameters`, `session_model_info`,
  `session_field_summary` (nodal or element/Gauss-point fields with extreme
  locations), `session_inspect_node`, `session_inspect_element`,
  `session_set_nodal_values` (with fix/free), `session_exec_python`, `session_status`,
  `session_list`, `session_close` — plus the `interactive_session` prompt.
  Sessions are single-stage, serial/OpenMP, bounded (`KRATOS_MCP_MAX_SESSIONS`),
  idle-timed-out, and do not survive a server restart.
- `session_exec_python` runs arbitrary Python inside the session and is disabled
  unless `KRATOS_MCP_ALLOW_EXEC=1`.
- `kratos://examples/load-stepping-cantilever`: a cantilever with a time-ramped
  tip load (`time_step` 0.125, 8 exact steps) with verified stepped results.
- Tests: Kratos-free session manager tests with a fake line-protocol worker, and
  integration tests checking the stepped result against a batch run.
- Docs: [interactive sessions tool reference](docs/tools/sessions.md), tutorial,
  architecture section, troubleshooting entries; notebooks
  `interactive_session.ipynb` and `interactive_fluid.ipynb`.
- `kratos://examples/coupled-blocks`: a mesh-based CoSimulation example — two
  Kratos structural blocks on separate meshes with non-matching interfaces
  (5 vs 7 nodes) coupled by Dirichlet–Neumann iteration through `kratos_mapping`
  (displacement mapped, reaction returned with `swap_sign` + `use_transpose`,
  strong Gauss–Seidel + Aitken). Verified against Kratos 10.4.4: both steps
  converge, the force through the interface is conserved (999998.2 N of 1e6 N)
  and the response is linear in the load ramp. Tutorial section 5 in
  `docs/tutorials/cosimulation.md`.
- `validate_case` / `validate_project_parameters` on a CoSimulation case now read
  each Kratos-wrapped sub-solver's mesh and report any interface
  `model_part_name` that is not its root model part or a SubModelPart, before
  launch; mapped data with differing `dimension`s produce a warning.
- Sub-solver VTK output of CoSimulation runs is indexed in `result-index.json`
  (one series per sub-solver model part), so `results_time_history` and
  `results_compare` apply to it.
- **Partitioned FSI cases** (Kratos' FSIApplication): `run_simulation` detects
  `solver_settings.solver_type: "partitioned"` and drives it with `FsiAnalysis`
  (ordinary `(model, parameters)` constructor, so no runner adapter). New
  `create_fsi_project` tool scaffolds a channel with an elastic floor (monolithic
  fluid + structure + ALE mesh solver, non-matching interface meshes, any FSI
  accelerator). `validate_case` / `validate_project_parameters` statically diagnose
  the nested solver blocks, domain-size and model-part mismatches, per-mesh
  sub model part references, and the interface pair (bare instead of full model-part
  names, missing/empty/unlisted interfaces, mapper count and faces, interfaces
  not on the same region) before launch; deep mode checks the five required
  applications are compiled. `explain_project_parameters` returns `kind: "fsi"`.
  `results_convergence` / `job_status` parse `STEP =`/`TIME =` banners,
  `FSI non-linear iteration` residual lines and the (otherwise silent)
  `maximum iterations N reached` non-convergence warning. New pure-Python module
  `fsi.py`.
- `kratos://examples/fsi-flexible-flap`: the Mok flexible-flap benchmark from
  Kratos' FSI tests, adapted to Kratos 10.x (full interface model-part names,
  placeholder `python_process` entries dropped, VTK output) and verified against the
  upstream control-node history (mesh displacement within 0.4 %, pressure within
  1 % over 30 steps); tutorial `docs/tutorials/fsi.md`.
- Tests: `tests/test_fsi.py` (Kratos-free) and kratos-marked example tests for both the
  Mok case and the generated case.

- **Adaptive remeshing (MeshingApplication / MMG)**: new `mesh_remesh` tool remeshes an
  `.mdpa` offline in a worker (`remesh_mdpa` op; `meshing_support` reports whether
  MMG is compiled in) with a `levelset` (sphere/box region or signed-distance
  expression), `hessian` (analytic nodal field, optional repeated passes) or `uniform`
  strategy, size bounds, frozen sub model parts and field transfer checks, and returns
  before/after mesh summaries plus a boundary preservation report. New
  `add_remeshing_process` tool adds a setup-time `mmg_process` (remeshed before the
  solver initializes) to a ProjectParameters.json; `validate_case` checks MMG
  availability and warns about remeshing inside the solution loop (verified to crash
  the standard thermal solver with SIGSEGV); `explain_project_parameters` lists a
  `remeshing` summary. Cases with an `mmg_process` are rejected by studies,
  checkpoints, MPI launch and interactive sessions. New pure-Python module
  `remesh.py` (AST-whitelisted expressions, request building, reports).
- `kratos://examples/adaptive-remeshing` and tutorial `docs/tutorials/adaptive-remeshing.md`:
  verified against Kratos 10.4.4 - a 3-pass hessian-adapted mesh (724 elements) has about
  half the interpolation error of uniform meshes of 657-796 elements, and a thermal
  case is refined from 231 to 1608 nodes at setup.
- Tests: `tests/test_remesh.py` (Kratos-free, plus capability-gated real MMG tests).
- **Remeshing from solved fields**: `mesh_remesh(strategy="hessian", field_variable=...,
  field_results=<result-index.json | ascii .vtk/.vtu>, field_time/field_step)` drives the
  hessian metric from a field read from a VTK result (points matched to mesh nodes by
  coordinates), transfers it, and writes it to the output `.mdpa` as `Begin NodalData`
  (`fields_written`), carrying it across `passes`. `mdpa.parse`/`dumps` now round-trip scalar
  `NodalData` blocks (`Mdpa.nodal_data`), and the input mesh's own scalar `NodalData` is
  transferred too. Verified against Kratos 10.4.4 on the thermal example (231 -> 1648 nodes
  /elements after one pass); `strategy: spr` remains on the roadmap. New helpers
  `remesh.field_from_result` / `carry_fields`.

### Changed

- MPI launch, studies, checkpoints/resume, interactive sessions and Flowgraph
  export reject CoSimulation and partitioned FSI cases with an explicit message
  (CoSimulation previously failed with "Cannot infer analysis type").
- `results_convergence` / `job_status` progress no longer count the `STEP:`/`TIME:`
  banners that Kratos-wrapped sub-solvers print as extra coupling steps; the
  `time=… | step=N` banner is authoritative in a coupled log.

## 0.7.0 — 2026-10-01

### Changed

- Updated the Python dependency lock to clear the remaining Dependabot
  security alerts: PyJWT 2.15.1 (from 2.13.0), tornado 6.5.10 (from 6.5.8)
  and urllib3 2.8.0 (from 2.7.0). All three are transitive dependencies of
  `mcp[crypto]`, the Jupyter dev group and the `viz` extra respectively.
- Synchronized package versions at 0.7.0.

## 0.6.0 — 2026-10-01

### Added

- Local MPI launch for `run_simulation` (`mpi_ranks`, `omp_threads`): launcher
  discovery (`KRATOS_MPI_LAUNCHER`, `KRATOS_MPI_ARGS`), parallel_type
  validation and an MPI-support probe of the Kratos build that fail before any
  job is created, per-rank logs (`job_logs(rank=N)`, `rank_logs` in
  `job_status`), launch provenance in the manifest and replay by `job_rerun`.
- Cancellation and status now track the whole process group, so no MPI rank
  outlives `job_cancel` and stray ranks are killed once the launcher exits.
- `kratos_check_installation` reports MPI launcher, `KratosMultiphysics.mpi`
  and Trilinos availability.
- Capability-gated two-rank vs serial acceptance test (not yet run against a
  real distributed build).

## 0.5.0 — 2026-09-23

### Added

- Persistent parameter sweeps (JSON Pointer product/zip axes) and structured
  mesh-convergence studies, with six MCP tools, isolated provenance, bounded
  detached execution, verified coordinator recovery, probe responses and CSV.
- Durable queued study jobs with exclusive supervisor ownership, process-group
  cancellation, and recorded exit status independent of the MCP server lifetime.
- Cantilever study tutorial and numerical stiffness/refinement acceptance tests;
  the completed study item is removed from the roadmap, leaving MPI next.

- Managed checkpoint configuration, discovery, and verified resume into a new
  isolated serial single-stage job, with a structural dynamics acceptance test.
- Physical-time VTK indexes, point/cell time histories, CSV export, and
  tolerance-based history and matching-mesh field comparisons.

- This changelog and a prioritized roadmap with implementation gaps,
  Kratos source references, and acceptance probes.
- Roadmap links in the README and documentation navigation.
- Per-stage deep validation for orchestrated cases, with stage-qualified
  errors and explicit warnings when an optional solver application is absent.
- Versioned job manifests, SHA-256 input inventories, opt-in isolated case
  snapshots, explicit external-input mappings, and verified snapshot reruns.
- A runnable NACA0012 perturbation potential-flow fixture with upstream
  reference data, a resource, tutorial, and capability-gated integration test.

## 0.4.0 — 2026-09-19

### Changed

- Refreshed Python and documentation dependency locks, including anyio
  4.15.1, and updated Vite, esbuild, and PostCSS overrides.
- Synchronized package versions at 0.4.0.

## 0.3.1 — 2026-08-07

### Changed

- Refreshed the Python dependency lock and bumped package versions to 0.3.1.

## 0.3.0 — 2026-07-14

### Added

- Multi-stage project scaffolding and execution through Kratos' native
  Project and orchestrator, including shared model-part reuse.
- Process-default discovery through Python AST parsing, with defaults used
  when adding boundary conditions and output processes.
- Material and linear-solver presets, fractional-step fluid and potential-flow
  templates, and structured ProjectParameters explanations.
- Lossless ProjectParameters ↔ FlowGraph conversion tools.
- NACA0012 airfoil, lid-driven cavity, plasticity cube, and multi-stage
  examples; additional example resources, tutorials, and client notebooks.
- Visualization crop bounds for focusing on small bodies in large domains.

### Changed

- Updated FlowGraph links to its official KratosMultiphysics repository.
- Added license metadata to the documentation package.

## 0.2.0 — 2026-07-12

### Added

- Optional PyVista PNG rendering and GIF animation through isolated worker
  processes, with headless Xvfb support and visualization documentation.
- A bundled cantilever case with real mesh, parameters, materials, and
  integration checks.
- The `kratos-mcp-server` command alias alongside `kratos-mcp`.

### Changed

- Raised the MCP SDK minimum to 1.10 for tools returning images and metadata.

## 0.1.1 — 2026-07-12

### Changed

- Renamed the Python distribution from `kratos-mcp` to `kratos-mcp-server`.

## 0.1.0 — 2026-07-12

### Added

- Initial stdio MCP server with subprocess-isolated Kratos introspection,
  build-tree discovery, pip fallback, and installation tooling.
- Structural, thermal, and fluid case templates, materials and process
  editing, structured MDPA mesh generation, inspection, and validation.
- Background simulation jobs with persistent status, logs, progress, and
  cancellation, plus VTK summaries, probes, and convergence analysis.
- Example resources, workflow prompts, documentation, tests, and CI,
  documentation deployment, and release workflows.
