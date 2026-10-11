# Kratos MCP Server

MCP (Model Context Protocol) server exposing Kratos Multiphysics to AI
assistants: installation introspection, case scaffolding (ProjectParameters /
Materials / MDPA meshes), simulation execution as background jobs, VTK
post-processing, and pyvista result previews (PNG/GIF returned inline).
Python, `mcp` SDK (FastMCP), stdio transport.

## Environment

A local compiled build is optional: `src/kratos_mcp/kratos_env.py` resolves
Kratos from, in order, an explicit `KRATOS_PYTHONPATH`/`KRATOS_LIBS`, a
`KRATOS_ROOT` build tree, or a pip-installed `KratosMultiphysics` importable
by the server's own interpreter.

- `KRATOS_ROOT` (default `/home/vicente/src/Kratos`) → uses
  `$KRATOS_ROOT/bin/Release` as `PYTHONPATH` and `$KRATOS_ROOT/bin/Release/libs`
  as `LD_LIBRARY_PATH`.
- Overrides: `KRATOS_PYTHONPATH`, `KRATOS_LIBS`, `KRATOS_SOURCE` (source tree
  for macro parsing), `KRATOS_EXTRA_LIBS` (extra lib dirs; MKL under
  `/opt/intel/oneapi/mkl/latest/lib` is auto-detected — LinearSolversApplication
  needs `libmkl_rt.so.2`).
- **Pip fallback**: if no build tree resolves, `kratos_env.resolve()` probes
  `python -c "import KratosMultiphysics"` in a subprocess. The `kratos_install`
  tool (`tools/environment.py`) populates this by running `pip install` —
  official wheels are `KratosMultiphysics` (core), `Kratos<AppName>` per
  application (e.g. `KratosStructuralMechanicsApplication`), or
  `KratosMultiphysics-all` (everything) — Linux/Windows x86_64 only, no macOS
  wheels. `kratos_env.pip_install()`/`pypi_package_name()` do the mapping and
  subprocess call; running pip itself is fine in the server process (it never
  imports Kratos), unlike everything else Kratos-related.
- Manual incantation for ad-hoc Kratos scripts against a build tree:
  `PYTHONPATH=$KRATOS_ROOT/bin/Release LD_LIBRARY_PATH=$KRATOS_ROOT/bin/Release/libs:/opt/intel/oneapi/mkl/latest/lib python3 ...`
- Server state (jobs, bridge cache) lives in `~/.kratos-mcp/`
  (`KRATOS_MCP_HOME` overrides; tests set it to a tmp dir).

## The one hard rule

**Never import KratosMultiphysics in the server process.** It prints an ASCII
banner on import (corrupts the stdio JSON-RPC stream) and its C++ core can
abort the process. All Kratos access goes through subprocesses:

- `bridge.py` → spawns `worker.py` for short ops (introspection, validation,
  deep mdpa read). Results travel via a `--result-file` JSON file, never
  stdout. Cacheable ops are cached on disk keyed by build fingerprint.
- `jobs.py` → spawns `runner.py` detached for simulations. Job state persists
  in `~/.kratos-mcp/jobs/<id>/` (`meta.json` + `stdout.log`) and survives
  server restarts; orphaned jobs are re-evaluated from pid liveness + log tail.
  `runner.py` runs a single `AnalysisStage` by default, but detects a
  multi-stage case (`orchestrator` + `stages` keys) and drives it via Kratos'
  `Project` + registry-resolved orchestrator class instead (the entry point
  Kratos' own `test_sequential_orchestrator` uses) — no `jobs.py` change.
  A CoSimulation case (`solver_settings` with `coupling_sequence`/`coupled_solvers.*`,
  detected by `cosim.is_cosim`) is driven by `CoSimulationAnalysis(parameters)` —
  its constructor is `(cosim_settings, models=None)`, so it is NOT in
  `ANALYSIS_CLASSES`. The VTK/restart output-index instrumentation IS installed for it
  (class-level patch, so Kratos-wrapped sub-solvers' VTK output lands in
  `result-index.json`, one series per sub-solver model part).
  Isolated runs additionally keep `manifest.json`, an immutable `snapshot/`,
  and a separate `execution/` directory; `job_rerun` verifies snapshot hashes
  and the Kratos build fingerprint before launching a fresh job.

- `sessions.py` → spawns `session_worker.py`, a LONG-LIVED worker that keeps a
  `Model` + initialized `AnalysisStage` in memory and is driven over JSON lines
  (stdin requests, a private dup of the original stdout for responses; fds 1/2
  are re-pointed at `sessions/<id>/session.log` before Kratos is imported).
  See "Interactive sessions" below.

`worker.py`, `runner.py` and `session_worker.py` are the ONLY modules that
import Kratos, and they only run inside subprocesses with the env vars injected.

The same class of rule covers pyvista/VTK: its OpenGL init can abort the
process on headless/misconfigured systems, so `tools/visualize.py` spawns
`render_worker.py` (the ONLY module that imports pyvista) as a plain
subprocess — no Kratos env needed — with the same `--result-file` JSON
convention. When no display is available it starts a private Xvfb (if
installed) via `-displayfd`.

## Commands

- `uv sync` — install deps (`mcp`, `meshio`, `numpy`; dev: pytest, ipykernel,
  nbclient, nbformat)
- `uv sync --extra gmsh` — adds gmsh for `mesh_generate_gmsh` (optional)
- `uv sync --extra viz` — adds pyvista + imageio for `results_render`/
  `results_animate` (optional; without it those tools return an install hint)
- `uv run kratos-mcp` — run the server (stdio)
- `uv run pytest -m "not kratos"` — unit tests, no Kratos needed
- `uv run pytest -m kratos` — integration tests against the real build
  (cantilever, thermal bar, naca airfoil, and capability-gated potential flow
  end-to-end with physics assertions)
- `uv run python tests/smoke_client.py` — scripted stdio MCP client smoke test
- `npm run docs:dev` / `npm run docs:build` — VitePress docs

## Layout

- `src/kratos_mcp/server.py` — FastMCP instance + main()
- `src/kratos_mcp/tools/` — one module per tool category (`environment`,
  `scaffold`, `mesh`, `mesh_edit`, `geometry`, `project`, `simulation`, `postprocess`, `visualize`, `resources`,
  `prompts`), each exposing `register(mcp)`; `register_all` in `__init__.py`
- `src/kratos_mcp/session_worker.py` / `sessions.py` / `tools/sessions.py` —
  interactive stateful sessions (see "Interactive sessions"); the first is the
  third Kratos-importing module.
- `src/kratos_mcp/render_worker.py` — pyvista/VTK rendering, subprocess-only
  (see above)
- `src/kratos_mcp/mdpa.py` — pure-Python MDPA parse/write/inspect/validate +
  structured mesh generators (line/rectangle/box with named boundary parts).
  Beyond the mesh it models `Tables`, `ElementalData`/`ConditionalData`
  (verbatim values keyed by entity id), scalar + vector + flag `NodalData`
  (fixed flags kept) and per-`SubModelPart` data/table/property references;
  only truly unknown blocks (`Constraints`, …) land in `skipped_blocks`.
- `src/kratos_mcp/mesh_edit.py` — Kratos-free editing of an `Mdpa` (nodes, elements/conditions/
  geometries, sub model parts, selectors, transform/merge/renumber/extract, `extrude`,
  `convert_cells`, `extract_boundary`, `quality`, plus `set_elemental_data`/`set_table`/
  `delete_table` for the data blocks). Raises `ValueError`; `tools/mesh_edit.py`
  wraps every tool in `_tool_errors` and writes in place or to `output=` (refusing in-place
  edits only when `Mdpa.skipped_blocks` is non-empty: unmodelled blocks would be lost).
  Quadratic cells (tri6, quad8/9, tet10, hex20/27, wedge15, line3; corners-first Kratos
  ordering verified against `kratos/geometries/*`) map to their linear kind for
  measures/boundary/extrusion; `convert_cells` has a `quadratic_to_linear` mode.
  `mdpa.py` also owns `find_part`/`part_paths` (the one dotted-path
  lookup), `expected_node_count` (type-name suffix -> node count, used by `validate`) and
  geometries (`Mdpa.geometries`, `SubModelPart.geometries`). Boundary conditions it creates are
  oriented with the domain on the LEFT; Kratos 10.4 then treats a positive
  `POSITIVE_FACE_PRESSURE` as tension (verified: plate + Lame cylinder), so compressive
  pressure needs a negative value.
- `src/kratos_mcp/geometry.py` — numpy-only parametric generators (annulus, disc, cylinder via
  `extrude`, sphere, ear-clipped polygon with hole bridging + `target_size` subdivision)
  following `create_rectangle_mesh`'s part naming (`side_<i>`, `hole_<k>`).
- `src/kratos_mcp/mesh_io.py` + `gmsh_worker.py` — meshio import/export (`from_meshio`/
  `to_meshio`, physical groups <-> sub model parts; linear and quadratic cells, except
  15-node wedges which meshio 5.3 itself cannot represent) and the optional gmsh generator.
  `gmsh_worker.py` is the ONLY module that imports gmsh (native library; same subprocess +
  `--result-file` rule as `render_worker.py`; needs libGLU/libXft on Linux). `gmsh` is the
  `gmsh` extra; tests skip when it is missing.
- `src/kratos_mcp/project_edit.py` — Kratos-free ProjectParameters/Materials editing used by
  `tools/project.py` (`scope(params, stage)` resolves `stages.<n>.stage_settings`, JSON Pointer
  get/set/delete, process list helpers, `build_process_block` merging `process_catalog`
  defaults, lenient `//`-comment reader, atomic writer, materials upsert/remove).
  `add_boundary_condition` reuses `scope`/`save_json`.
- `src/kratos_mcp/source_catalog.py` — parses `KRATOS_REGISTER_ELEMENT/
  CONDITION/CONSTITUTIVE_LAW` macros from the Kratos C++ sources (there is no
  runtime registry for these); also enumerates `*_process.py` / `*_solver.py`
  files (`python_process_files()` is the path index `process_catalog` reuses)
- `src/kratos_mcp/process_catalog.py` — recovers a Kratos process' default
  settings by AST-parsing its `*_process.py` (`ValidateAndAssignDefaults`
  block, or a `GetDefaultParameters` classmethod). Pure `ast`/text, no Kratos
  import — same trust model as `source_catalog`. Ported from Flowgraph's
  `parse-processes.py`. Backs `kratos_get_process_defaults`,
  `kratos_list_processes(with_defaults=True)` and the auto-filled defaults in
  `add_boundary_condition`/`add_output_process`. Output processes validated in
  C++ (e.g. `vtk_output_process`) have no Python defaults → returns None (the
  enrichment then no-ops, keeping the hand-authored block).
- `src/kratos_mcp/cosim.py` — Kratos-free CoSimulation support: `is_cosim`
  (the single detection point used by every rejection), static `validate` (names,
  data, transfer operators, convergence refs, SDoF input files; Kratos-wrapped
  sub-solvers validated through a callback `scaffold` injects), `summarize`
  (explain) and `compose` (backs `create_cosim_project`). Studies, checkpoints,
  MPI, sessions and Flowgraph export reject cosim cases.
- `src/kratos_mcp/fsi.py` — Kratos-free partitioned FSI support: `is_fsi`
  (`solver_settings.solver_type` `partitioned`/`partitioned_embedded`, the single
  detection point used by every rejection), static `validate` (nested
  fluid/structure/mesh blocks, domain sizes, per-mesh sub model part references,
  the interface pair), `summarize` (explain) and the flexible-wall generators
  behind `create_fsi_project`. Unlike CoSimulation, `FsiAnalysis`
  (`KratosMultiphysics.FSIApplication.fsi_analysis`) keeps the `(model, parameters)`
  constructor, so `runner.py` just maps `partitioned*` to it via
  `ANALYSIS_CLASSES["fsi"]`. Studies, checkpoints (no FSI restart), MPI, sessions
  and Flowgraph export reject FSI cases.
- `src/kratos_mcp/remesh.py` — Kratos-free adaptive remeshing support: `compile_expression`
  (AST whitelist for the `x/y/z` expressions that are evaluated again in the worker),
  `region_expression` (sphere/box → signed distance, `inside` clamps at 0), `build_request`
  (validation + `mmg_process` settings + `assign`/`check` expression lists, or per-node
  `values` from `field_from_result` for a solved-field hessian), `carry_fields`, `report`
  (before/after `Mdpa` summaries, boundary preservation by node bounding box),
  `process_block`/`validate`/`has_remeshing` for in-run `mmg_process` blocks (the single
  detection point used by every rejection: studies, checkpoints, MPI, sessions).
  Backs `mesh_remesh` (`tools/mesh.py`; worker ops `meshing_support`, `remesh_mdpa`) and
  `add_remeshing_process` (`tools/scaffold.py`).
- `src/kratos_mcp/examples/adaptive_remeshing/` — `plate.mdpa` + `request.json` (the
  offline hessian example) and `setup_remesh/` (a thermal case whose mesh has a
  `Begin NodalData DISTANCE` block, remeshed at setup). Served by
  `kratos://examples/adaptive-remeshing` with verified numbers in
  `ADAPTIVE_REMESHING_RESULT`. Same "re-verify with a real run" rule.
- `src/kratos_mcp/project_explain.py` — pure-JSON structured summary of a
  ProjectParameters.json (analysis type, solver, processes, per-stage for
  multistage); backs `explain_project_parameters` and feeds `flowgraph.py`.
- `src/kratos_mcp/flowgraph.py` — lossless ProjectParameters ↔ Flowgraph
  (litegraph) `graph.json` conversion. Each node carries a Flowgraph-compatible
  type/position for visual loadability AND its exact JSON fragment under a
  `_role` marker; `graph_to_project` reconstructs from the markers (not the
  links), so `import(export(p)) == p`. Backs `tools/interop.py`.
- `src/kratos_mcp/templates/` — case templates as data: `registry.json`
  (metadata + placeholder defaults) + `<name>/ProjectParameters.json.tpl` +
  `Materials.json.tpl`. Substitution: quoted `"{{key}}"` → JSON-typed value,
  bare `{{key}}` → text. Templates: `structural_static/dynamic/modal`,
  `thermal_transient/stationary`, `fluid_transient` (monolithic),
  `fluid_fractional_step`, `potential_flow` (needs
  CompressiblePotentialFlowApplication — modelled on the Kratos NACA0012
  perturbation test; not always compiled in local builds). Plus two
  preset data files (not per-case dirs): `material_presets.json` (constitutive
  laws + default variables, seeded from Flowgraph's material nodes) and
  `linear_solvers.json` (drop-in `linear_solver_settings` blocks). Both are
  surfaced by `list_material_presets`/`list_linear_solver_presets`;
  `create_materials` accepts a `preset=` per entry.
- `src/kratos_mcp/logparse.py` — step/convergence extraction from job logs
- `src/kratos_mcp/examples/cantilever/` — real `mesh.mdpa` +
  ProjectParameters.json + Materials.json files (a coarse 4x1 case), read
  verbatim by the `kratos://examples/cantilever` resource in
  `tools/resources.py`, not rendered from `templates/` at request time. If
  `structural_static`'s template changes, regenerate these files by hand
  and re-verify the numbers with a real run (recipe in
  `tests/test_examples.py` and the cantilever-beam.md tutorial).
  `thermal-bar` stays dynamically rendered via `_example_bundle()`.
- `src/kratos_mcp/examples/naca_airfoil/` — real `mesh.mdpa` (~21k nodes, 3.6
  MB — a genuine externally-authored/GiD unstructured mesh reused from
  Kratos' own examples repo, `fluid_dynamics/validation/
  compressible_naca_0012_Ma_0.8`) + ProjectParameters.json + Materials.json,
  read by the `kratos://examples/naca-airfoil` resource. Unlike cantilever,
  the resource does **not** embed the raw mesh text (too large to be useful
  in a response) — it gives a `mdpa_inspect`-style summary instead. This is a
  DELIBERATE SIMPLIFICATION of the literal reference case (which is
  transonic/compressible/multi-stage, unsupported by this server): only the
  airfoil geometry is reused, driven as incompressible laminar flow through
  the existing `fluid_transient` template. If that template changes,
  regenerate `ProjectParameters.json`/`Materials.json` by hand and re-verify
  the Cd/Cl numbers with a real run (recipe in `tests/test_examples.py` and
  the naca-airfoil.md tutorial).
- `src/kratos_mcp/examples/load_stepping_cantilever/` — verbatim example for
  interactive sessions: 5 m x 0.5 m cantilever (20x4 quads, mesh from
  `mdpa.create_rectangle_mesh`), tip line load `modulus: "1.0e6*t"`, and
  `time_step: 0.125` (exact in binary → exactly 8 steps; `0.1` accumulates to
  0.9999… and runs a 9th step). Served by `kratos://examples/load-stepping-
  cantilever` via `_verbatim_example`; verified numbers (tip uy = -7.867e-3 m at
  t=1) in `LOAD_STEPPING_RESULT`; ProjectParameters is hand-authored (copy of the
  cantilever's with the ramp). Same "re-verify with a real run" rule.
- `src/kratos_mcp/examples/{lid_driven_cavity,plasticity_cube,multistage_load_steps}/`
  — three more verbatim on-disk examples, meshes GENERATED by this server's own
  `mdpa` generators (small enough to embed): `lid_driven_cavity` (Re=100
  monolithic cavity, 121 nodes, a single-node `corner` sub-model-part pins the
  pressure), `plasticity_cube` (single 8-node hex, `analysis_type: non_linear`,
  von Mises plasticity via `create_materials(preset=...)`; ProjectParameters is
  hand-authored, NOT template-rendered — the linear `structural_static` template
  can't express plasticity), and `multistage_load_steps` (an `orchestrator`/
  `stages` case composed by `create_multistage_project`, two load steps sharing
  the mesh). Each has an INTRO/RESULT prose pair + `@mcp.resource` in
  `resources.py`, verified numbers baked in, and structural + `@pytest.mark.kratos`
  tests in `tests/test_examples.py`. Same "re-verify with a real run if a
  template changes" rule. The three cheap dynamic bundles (`channel-flow`,
  `modal-box`, and `dynamic-cantilever`) are just `_example_bundle("<template>",
  "<hint>")` resources. `potential_flow/` is a verbatim NACA0012
  perturbation-compressible fixture copied from Kratos revision
  `e740da832999e4da58dd9457f35143274edb4992`, with runnable parameters and
  upstream reference data; it needs CompressiblePotentialFlowApplication and
  LinearSolversApplication.
- `src/kratos_mcp/examples/coupled_sdof/` — CoSimulation example: two Python-only
  SDoF oscillators as a mass-spring chain, `gauss_seidel_strong` + Aitken, no
  mesh (ProjectParameters.json + sdof_1.json + sdof_2.json, served by
  `kratos://examples/coupled-sdof`, custom loader in `resources.py`). Verified
  numbers in `COUPLED_SDOF_RESULT` (125 coupling iterations, exact one-step-lag
  interface exchange); needs only KratosCoSimulationApplication. Same "re-verify
  with a real run" rule.
- `src/kratos_mcp/examples/coupled_blocks/` — mesh-based CoSimulation example: two
  Kratos structural blocks (`solver_wrappers.kratos.structural_mechanics_wrapper`,
  meshes from `mdpa.create_rectangle_mesh`, interfaces of 5 vs 7 nodes) coupled
  Dirichlet-Neumann via `kratos_mapping` (`nearest_element`; reaction back with
  `swap_sign`+`use_transpose`), strong Gauss-Seidel + Aitken. Served by
  `kratos://examples/coupled-blocks`; verified numbers in `COUPLED_BLOCKS_RESULT`
  (13 iterations/step, 999998.2 N through the interface). Gotchas encoded in it:
  the load-receiving interface needs `PointLoadCondition2D1N` (plus line
  conditions for the mapper), vector data needs `"dimension"`, and the
  convergence criterion must watch the Neumann side's produced data (the imposed
  side's first residual is 0/0 = false convergence). Needs CoSimulation, Mapping,
  StructuralMechanics and LinearSolvers applications. Same "re-verify" rule.
  `cosim.validate` cross-checks every Kratos-wrapped `data.*.model_part_name`
  against that solver's mesh (callback injected by `scaffold`), and
  `logparse` treats the `time=… | step=N` banner as authoritative in a coupled
  log (sub-solvers print their own `STEP:`/`TIME:`).
- `src/kratos_mcp/examples/fsi_flexible_flap/` — the Mok flexible-flap benchmark
  from Kratos' own FSIApplication tests (`MokBenchmarkTest`: externally authored
  GiD meshes, 4556 fluid / 505 structure nodes, plus the upstream
  control-node history `reference_control_nodes.json`). Adapted to Kratos 10.x:
  interfaces and fluid skin/volume parts as FULL model-part names, upstream's
  placeholder `python_process` entries dropped, GiD → VTK output, `end_time` 3 s.
  Served by `kratos://examples/fsi-flexible-flap` with mdpa summaries instead of
  the raw meshes. Verified numbers (mesh displacement within 0.4 %, pressure
  within 1 % of upstream; 30 steps converge in 3-6 FSI iterations, ~50 s) are in
  `FSI_FLEXIBLE_FLAP_RESULT` and asserted by `tests/test_examples.py`. The
  upstream `TIME` array has 249 entries against 250 values, so Kratos'
  `FromJsonCheckResultProcess` cannot consume it — tests compare VTK output.
  The generated counterpart is `create_fsi_project` (`fsi.flexible_wall_*`):
  `FSI_FLEXIBLE_WALL_NOTE` holds its numbers (-2.05e-2 floor deflection).
  Same "re-verify with a real run" rule.
- `notebooks/{cantilever,naca_airfoil,fluid_cavity,materials,multistage,
  interactive_session,interactive_fluid}.ipynb`
  — MCP
  *client* notebooks (use `mcp.client.stdio` directly, not the server code)
  walking through most tools/resources/prompts against the real example
  cases; all outputs are baked in from a real run, not placeholders.
  Regenerate after a workflow-affecting change by rebuilding with `nbformat`
  and re-executing with `nbclient`/`jupyter nbconvert --execute` against a
  kernel that has this project's `.venv` (`uv sync --extra viz --group dev`,
  then `python -m ipykernel install --user --name <name>`) — same
  "re-verify with a real run" rule as the example resources. `naca_airfoil.
  ipynb` takes ~5 minutes to execute (the 21k-node mesh's 40-step run is
  ~4 minutes of that) — budget accordingly when regenerating it.
  Gotcha: don't hold the `stdio_client`/`ClientSession` context managers open
  across cells with a bare `AsyncExitStack` — each Jupyter cell's top-level
  `await` runs in a new asyncio Task, and anyio's cancel scopes require
  entering and exiting in the *same* Task, so closing in a later cell raises
  "Attempted to exit cancel scope in a different task" and leaks the server
  subprocess. Fix: run connect-through-disconnect inside one persistent
  background task (`asyncio.create_task`) that stays alive on an
  `asyncio.Event`, signalled (not re-entered) from whichever cell closes it.

## Kratos gotchas learned the hard way

- Load-bearing conditions must be the `*LoadCondition*` types
  (`LineLoadCondition2D2N`, `SurfaceLoadCondition3D4N`); the generic
  `LineCondition2D2N`/`SurfaceCondition3D4N` are geometric dummies that
  silently contribute nothing (zero RHS, zero displacement).
- The convection-diffusion solver REPLACES mesh elements at import
  (`element_replace_settings`); thermal meshes use generic `Element2D3N` +
  `ThermalFace2D2N`. It must be a simplex mesh (triangles/tets) — its
  `TetrahedralMeshOrientationCheck` fails on quads with "condition without
  any corresponding element".
- `solver_type: stationary` still defaults to the transient EulerianConvDiff
  element; true steady state needs `element_replace_settings: {"element_name":
  "LaplacianElement", ...}` (our `thermal_stationary` template does this).
- Static analyses run exactly one step via `time_step: 1.1 > end_time: 1.0`
  (standard Kratos test convention).
- Multi-stage: the orchestrator shares ONE `Model` across stages, so a later
  stage that re-imports the mesh into a model part an earlier stage already
  populated aborts with "a node with the same Id already exists". Stages that
  share a mesh must set `model_import_settings.input_type:
  "use_input_model_part"` (reuse) after the first stage's `mdpa` import — this
  is exactly what `create_multistage_project` does when a later stage's
  `model_part_name` matches an earlier one's.
- CoSimulation: SDoF wrapper data must be `"location": "model_part"`;
  `SCALAR_DISPLACEMENT` reports the last accepted displacement, so the partner's
  root-point displacement lags one step (documented in the example, don't
  "fix" it). Constant-relaxation accelerator takes `alpha`, Aitken `init_alpha`.
  A solver in `solvers` that's missing from `coupling_sequence` aborts Kratos.
  Coupling-iteration log lines are `Coupling iteration: k / N`,
  `### CONVERGENCE WAS ACHIEVED ###` / `XXX CONVERGENCE WAS NOT ACHIEVED XXX`,
  steps are `time=T | step=N:` (parsed in `logparse`).
- `Kernel.Get*VariableNames()` return one newline-separated string, not lists.
- `KM.Parameters` tolerates `//` comments; plain `json.loads` on Kratos test
  files may fail.
- Kratos resolves mdpa/materials paths relative to the CWD → `runner.py`
  chdirs into the case directory.
- Fluid solvers (`fluid_solver.py`'s `_ReplaceElementsAndConditions`) replace
  ALL elements/conditions unconditionally via `ReplaceElementsAndConditionsProcess`
  — matched purely by node count + `domain_size`, not by the original type
  name. An externally-authored mesh with generic/unrelated element or
  condition names (e.g. `naca_airfoil`'s `LineCondition2D2N`, not the
  registry's informational `WallCondition2D2N` default) works fine as-is;
  there's no need to rename anything before import.

- FSI (FSIApplication): interfaces must be FULL model-part names
  (`FluidModelPart.X`) — a bare sub model part name fails in `PrepareModelPart`
  with "not found as root-ModelPart" (older Kratos accepted it; upstream's test
  files still use bare names). FSI non-convergence only logs `FSI non-linear
  maximum iterations N reached` and the job still exits 0 — read
  `results_convergence`. A static elastic wall under an incompressible fluid
  diverges (NaN residuals) unless
  `thickness*mu*L^2/(E*H^3*dt) << 1` and the Reynolds number is low; the
  generator's defaults (rho=mu=1, E=200) are a verified stable regime and a
  Reynolds-1000 variant oscillated even when stiff. `meshio` cannot read
  Kratos' `binary` VTK — FSI outputs use `ascii`. Float time accumulation:
  `end_time` 5.0 at dt 0.1 runs 51 steps, 3.0 runs 30.

- Remeshing (MeshingApplication): the pip `KratosMeshingApplication` wheel bundles MMG.
  `mmg_process` sub model part names (`fix_contour_model_parts`) are relative to the root
  model part. Remeshing INSIDE the solution loop segfaults standard solvers (SIGSEGV after
  the first remesh; their strategy is bound to the old mesh), so only `initial_remeshing`
  is offered: process `ExecuteInitialize` runs before `AnalysisStage._InitializeInternals`.
  The metric field must already be on the nodes then (`Begin NodalData`, listed in
  `auxiliary_variables_list`); `assign_scalar_variable_process` runs later. A hessian
  metric from one pass on a mesh coarser than the feature barely helps - `passes=3`
  halves the interpolation error. `mdpa.parse`/`dumps` keep top-level
  `Begin NodalData` blocks (scalar values with fixed flags, vector rows verbatim,
  flag rows), but `ModelPartIO` writes none (`MESH_ONLY`), so the worker returns the
  `write_fields` values and `mesh_remesh` splices them into the output with the Python
  writer, then feeds them to the next pass (`remesh.carry_fields`, assign `source: "mesh"`).
  A solved-field hessian (`field_results`) is read by `remesh.field_from_result`: VTK points
  are matched to node ids by COORDINATES (Kratos' ASCII VTK of the root model part happened
  to be in id order in the verified thermal case, but that is not assumed); `binary` VTK is
  unreadable by meshio. MMG's interpolation of a sharp front undershoots (-0.03 -> -4.7 in
  the thermal example); `transfer` reports value ranges for field-sourced data. MMG may coarsen straight boundaries; geometry
  (bounding box) is what is preserved, `freeze_sub_model_parts` pins the nodes.

## MCP / pyvista gotchas

- Tools that return a mixed `[dict, Image]` list (inline image + metadata)
  must be registered with `@mcp.tool(structured_output=False)`, or FastMCP's
  output-schema validation chokes; `Image` comes from `mcp.server.fastmcp`.
  This is why `pyproject.toml` requires `mcp>=1.10`.
- `imageio` is needed by pyvista's `open_gif` but is NOT a pyvista core
  dependency — the `viz` extra lists it explicitly.
- `pyvista.start_xvfb()` was removed in pyvista 0.48; `render_worker.py`
  manages its own Xvfb via `-displayfd`.
- A missing GL context makes VTK SEGFAULT, not raise — try/except is
  useless. Never render in-process, not even in tests: tests go through the
  `render_worker` subprocess too (a crash becomes a skippable nonzero exit),
  and CI runs them under `xvfb-run`.
- `results_render`/`results_animate` take a `crop_bounds` ([xmin,xmax,ymin,ymax]
  or +zmin,zmax) applied via `mesh.clip_box()` before the camera fits the
  view — essential for a small body in a huge far-field CFD domain (e.g. a
  unit-chord airfoil in a 12.5-20 unit far field), otherwise an invisible
  speck at full-domain zoom.

## Conventions

- All server logging to stderr (stdout is the MCP transport).
- Tools return plain dicts; errors as `{"error": ...}` rather than raising
  (bridge failures include worker stdout/stderr tails for diagnosis).
- Blocking work inside async tools goes through `anyio.to_thread.run_sync`.
- Tool inputs/outputs use absolute paths.
- Reference cases used for templates/tests:
  `applications/StructuralMechanicsApplication/tests/LinearTruss2D/2D2N/` and
  `applications/ConvectionDiffusionApplication/tests/basic_conv_diffusion_test/`
  in the Kratos tree. The `naca_airfoil` example instead reuses a mesh from
  the external KratosMultiphysics-Examples repo (`fluid_dynamics/validation/
  compressible_naca_0012_Ma_0.8`) — see that example's note in Layout above
  for what was simplified and why.

## Keep docs in sync

Every time you change code in this repo, check whether doc/, README.md, and this file need updating too — and update them if they do. Treat doc drift as part of the change, not a follow-up.


## MPI launch

- `mpi_launch.py` (no Kratos import) backs `run_simulation(mpi_ranks=,
  omp_threads=)`: launcher discovery (`KRATOS_MPI_LAUNCHER`, else `mpiexec`/
  `mpirun`; extra flags via `KRATOS_MPI_ARGS`), request validation, the
  `mpi_support` worker-op probe (`KratosMultiphysics.mpi`, Trilinos), rank
  detection and `/proc`-based process-group liveness. `jobs.start` validates and
  probes BEFORE creating the job dir, then wraps the runner command in the
  launcher; the launcher leads the process group.
- `runner.py` splits logs: rank 0 keeps stdout (`stdout.log`, so `logparse`
  works), ranks > 0 are `dup2`'d to `ranks/rank-<N>.log` before Kratos is
  imported. `jobs.cancel`/`refresh` use `_kill_group` and group liveness — never
  leader pid liveness alone for MPI jobs.
- Studies, checkpoints and multi-stage cases reject MPI. `tests/test_mpi_launch.py`
  uses a fake launcher script; `tests/test_mpi_integration.py` (two-rank vs
  serial) is capability-gated and has not been run against a real MPI build —
  verify it first when one is available.

## Checkpoints and time-series analysis

- `checkpoints.py` backs `configure_checkpoints`, `job_checkpoints`, and
  `job_resume`. Resume requires a terminal isolated serial single-stage job;
  it verifies snapshot/build/checkpoint hashes and copies the checkpoint into
  `restart_input/` in a new snapshot. Native save output remains separate.
- `runner.py` instruments native restart/VTK output calls for serial single-stage
  runs, publishing completed-file metadata atomically in `checkpoint-index.json`
  and `result-index.json`. These indexes record actual model-part time/step.
- `result_series.py` backs `results_time_history` and `results_compare` with
  meshio/numpy, including point/cell associations and exclusive-create CSV
  export. Full fields require identical exported meshes; time matching never
  interpolates. External results require explicit physical-time records.
- `tests/test_restart_integration.py` compares resumed structural dynamics
  displacement against an uninterrupted run, and exercises rerun/re-resume.


## Parameter and mesh studies

- `studies.py` backs six `study_*` tools in `tools/studies.py`: frozen inputs,
  product/zip JSON Pointer axes, structured resolution levels, probe responses,
  convergence differences and exclusive CSV export. Study state persists in
  `~/.kratos-mcp/studies/<id>/`. Serial single-stage POSIX cases only.
- `study_coordinator.py` owns an exclusive study lock and schedules bounded
  independent children. `jobs.start(..., _defer_launch=True, _job_id=...)` prepares
  durable queued identities; `jobs.launch_prepared` starts `job_supervisor.py`.
  The supervisor claims a queued job exactly once under its ownership lock and
  records the actual runner exit status. The supervisor and runner share a
  process group. No native imports occur in the coordinator or supervisor.
- Resume applies only to interrupted coordinators, verifies base/child inventories
  and build fingerprint, adopts active children and continues queued work.
  Cancellation preserves results; it cannot be resumed. Job reruns remain separate
  from their original study. Probe errors never change a successful job's state.
- `tests/test_studies.py` covers validation and lifecycle races with real dummy
  subprocesses; `tests/test_studies_integration.py` verifies inverse stiffness
  response, mesh refinement, failure isolation and detached recovery. The numerical
  cases require StructuralMechanicsApplication and LinearSolversApplication.


## Interactive sessions

- `session_worker.py` (Kratos-importing, subprocess-only) holds a `KratosSession`
  (model, parameters, stage). Analysis-class resolution reuses `runner.py`'s
  `ANALYSIS_CLASSES`/`detect_analysis_type`/`find_analysis_class_in_module`
  (runner imports only stdlib at module level, so importing it is safe).
  `advance` replays one `RunSolutionLoop` iteration per step
  (`KeepAdvancingSolutionLoop` → `_AdvanceTime` → `InitializeSolutionStep` →
  `SolveSolutionStep` → `FinalizeSolutionStep` → `OutputSolutionStep`); if Kratos
  changes `AnalysisStage` internals, update it and re-run
  `tests/test_session_integration.py` (stepped result must equal a batch run).
  Rejects multi-stage and non-OpenMP cases. The Kratos-free helpers at the top
  (`summarize`, `json_pointer_get`, `to_plain`) are unit-tested without Kratos.
- `sessions.py` (no Kratos) spawns it with `stdin/stdout=PIPE`,
  `start_new_session=True`, `env.build_env()` + own package root on `PYTHONPATH`
  (like `jobs.start`). A reader thread demultiplexes responses by request id; a
  timed-out request returns `busy` and keeps running (the reader, not the
  caller, clears `pending`). EOF/crash → `dead` with returncode + log tail.
  `close()` finalizes then `jobs._kill_group`s the process group; `atexit`
  closes all. Sessions are NOT durable: persisted metas of vanished sessions
  read as `lost`. `WORKER_MODULE` is monkeypatched by `tests/test_sessions.py`
  to `tests/fake_session_worker.py` (a Kratos-free protocol twin) — keep the two
  protocols in sync.
- Gotchas: Kratos vector variables (`Array1DVariable3`) cannot be `Fix`ed
  directly — fix the `_X/_Y/_Z` components (done in `set_nodal_values` and
  reported per component by `inspect_node`). `KM.KratosGlobals.GetVariable`
  resolves names (also `DISPLACEMENT_X`). `element.IsActive()`, not
  `Is(KM.ACTIVE)`. Element results via `CalculateOnIntegrationPoints`.
- `session_exec_python` is opt-in (`KRATOS_MCP_ALLOW_EXEC=1`), checked in both
  the tool and the worker. Other knobs: `KRATOS_MCP_MAX_SESSIONS` (4),
  `KRATOS_MCP_SESSION_IDLE_TIMEOUT` (3600 s).
- Tool/resource/prompt counts are quoted in `README.md` and `docs/tools/index.md`
  (currently 110 / 21 / 8) — bump both when adding any.
