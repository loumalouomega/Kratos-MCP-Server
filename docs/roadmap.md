# Kratos MCP Server roadmap

This document lists what is *not* built. Nothing here duplicates shipped functionality; where a feature partially exists, the shipped half is named and the gap is stated explicitly. Release history lives in [`CHANGELOG.md`](https://github.com/loumalouomega/Kratos-MCP-Server/blob/master/CHANGELOG.md), not here.

Effort key: **S** = days, **M** = a couple of weeks, **L** = a month or more, **XL** = a project in its own right. An item names a **probe** — the failing test that proves the gap — wherever one is cheap.

## How this file works

- **Sections are ordered, and the order is the recommendation.** Each section states what belongs in it; an item that sounds exciting does not move up for that reason. An empty section is removed, not kept as a placeholder.
- **A closed item is removed, not struck through.** Its history is the `CHANGELOG.md` entry and the feature's own `docs/` page; a partly closed item is narrowed to what remains (the `CLAUDE.md` change checklist rule).
- **Defect-shaped items go in a *Correctness debts* section at the top, regardless of size** — behaviour that loses data, mis-orients cells, does not terminate on valid input or fails silently is not a feature request, even when the fix and the feature are the same work. No confirmed defects are recorded here; the first defect found opens that section as §1 and renumbers the rest.
- **An item estimated from a doc, a `.d.ts` or a changelog alone says so** ("verify first") and names its probe; the code has repeatedly been more or less capable than its description.
- **[Non-goals](#non-goals-and-decisions-taken) record decisions already taken**, with their reasons, so they are not re-proposed as gaps.

These proposals were checked against server 0.4.0 and the local Kratos source at `~/src/Kratos` on 2026-09-22. Kratos paths below are relative to that checkout (override with `KRATOS_SOURCE` elsewhere). Source availability does not imply that an application is compiled or that a workflow has been run. Probes below are proposed acceptance tests, not tests already executed. Effort estimates cover a first usable implementation with documentation and tests.

---

## 1. Simulation lifecycle and result analysis

Extend the managed-job workflow before introducing new application families.

### MPI: real-build verification and scheduler integration — M

Local MPI launch (`mpi_ranks`, `omp_threads`, rank logs, group-wide cancellation, capability checks) is shipped and unit-tested with a fake launcher. What remains is the part that needs a distributed build: run `tests/test_mpi_integration.py` against a Kratos build with `KratosMultiphysics.mpi` and TrilinosApplication, fix whatever the first real run exposes (MPI import of a plain `.mdpa`, `point_output_process` file naming), and bake a verified reference case into an example. After that, add cluster launch: environment forwarding to remote ranks, a Slurm/`srun` launcher mode, and MPI-aware results (per-rank VTK partitions are not indexed today).

**Probe:** the existing two-rank test passes unmodified on a real build; a Slurm-launched two-rank run produces the same displacement within tolerance.

## 2. Additional Kratos workflows

These depend on optional applications and need small verified examples before being presented as supported templates. The shipped CoSimulation (`create_cosim_project`) and partitioned FSI (`create_fsi_project`) support is the pattern to follow: pure-Python static validation, a scaffold tool, and a worked example verified against real numbers.

### Remeshing inside the solution loop and from solved fields — L

One-shot `mesh_remesh` (levelset, analytic hessian, uniform; repeatable passes) and setup-time `add_remeshing_process` are shipped. What remains:

- **Remeshing during a run.** An `mmg_process` that fires inside the solution loop aborts standard solvers with SIGSEGV after the first remesh (verified with the thermal solver): the strategy, DOFs and builder stay bound to the old mesh. Needs a solver-side re-initialization hook (clear and rebuild after the `MODIFIED` flag) and a story for output continuity, restart and studies.
- **Metrics from solved fields.** `mesh_remesh` takes analytic expressions only; `.mdpa` carries no `NodalData` in this server's parser/writer. Drive the hessian from a field read from a VTK result (`result_series`), write the transferred field back, and expose superconvergent-patch-recovery error estimation (`strategy: spr`).
- **3D and surface meshes.** Only the 2D path has been run against real numbers; `MmgProcess3D` / `MmgProcess3DSurfaces` are wired but unverified.

**Probe:** adapt a 3D tetrahedral mesh to a solved structural field read from VTK and show the transferred stress/temperature error against a uniformly refined mesh.

### Optimization and reduced-order studies — XL

Build these separate experiments on the existing sweeps and response extraction. Kratos provides `applications/OptimizationApplication/python_scripts/optimization_analysis.py` and `applications/RomApplication/python_scripts/rom_manager.py`. The former already has a `(model, parameters)` constructor compatible with generic dispatch; the missing work is scaffolding, validation, response histories, and examples. ROM needs a separate lifecycle for training, validation, and online runs.

**Probe:** first reproduce a tiny optimization reference with its objective history; separately train a small ROM and report error on held-out parameters against full-order results. Store the training inputs and basis provenance.

## Non-goals and decisions taken

Recorded so they are not re-proposed as gaps.

- **No Kratos or PyVista imports in the MCP server process.** Keep native execution in workers to protect stdio transport and contain native crashes.
- **No replacement graphical editor.** FlowGraph import/export already provides the visual editing route; improve interoperability when a concrete gap arises.
- **No duplicate solver or process registry.** Extend existing runtime discovery and source parsing, using the configured Kratos build and source as authority.
- **No promise that every Kratos application is supported by a template.** The generic analysis-class entry point remains available; curated workflows need capability checks and numerical reference cases.

---

## Suggested sequencing

1. Verify local MPI on a real distributed build, then add cluster launch.
2. Extend remeshing to solved-field metrics and 3D (coupled-solver CoSimulation, partitioned FSI and offline/setup-time remeshing are shipped).
3. Evaluate optimization and ROM once reproducibility and quantitative comparisons are established. These are exploratory directions, not release commitments.
