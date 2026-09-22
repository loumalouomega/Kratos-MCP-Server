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

### Checkpoint discovery and restart — M

Persistent job tracking survives server restarts; it does not provide a tool to resume a stopped simulation from a Kratos checkpoint. Add checkpoint configuration, listing, and resume into a separate run directory, using `kratos/python_scripts/save_restart_process.py` and `restart_utility.py`. Hand-authored restart settings may already work through the generic runner; the gap is a validated, discoverable workflow.

**Probe:** interrupt a transient case after a checkpoint, resume it, and compare the final field with an uninterrupted run within numerical tolerance. Reject missing checkpoints with a useful diagnostic.

### Time histories and quantitative comparisons — M

VTK summaries, nearest-point probes, convergence logs, PNGs, and GIFs already exist. Add probes across a time series, CSV export, and reference-run comparison with absolute and relative tolerances. Preserve physical time and field association; initially require matching meshes for field differences.

**Probe:** a known transient field yields correctly ordered samples even when filenames sort differently from time; mismatched meshes produce a clear error.

### Parameter sweeps and mesh-convergence studies — L

Build on case snapshots and time-history comparisons to vary explicit JSON parameter paths or mesh resolutions. Queue independent jobs with bounded concurrency, collect response quantities, and retain each run's provenance. This is separate from the existing sequential multi-stage workflow, which can share a model between stages.

**Probe:** a three-value stiffness sweep creates independent cases and the expected displacement trend; one failed run does not discard other results.

### MPI launch and resource controls — L

MPI linear-solver presets already exist, but the job launcher starts a single Python process. Add explicit rank and thread counts, launcher capability checks, rank-aware logs, and cancellation of the entire process group. Start with one small distributed structural or fluid reference case; scheduler integration can follow once local MPI lifecycle handling is reliable.

**Probe:** a two-rank run agrees with its serial reference within tolerance, and cancellation leaves no worker ranks running. A missing MPI build fails before launch.

## 2. Additional Kratos workflows

These depend on optional applications and need small verified examples before being presented as supported templates.

### CoSimulation adapter and coupled example — L

Sequential orchestration already exists; coupled solvers exchanging data need a different integration. In `applications/CoSimulationApplication/python_scripts/co_simulation_analysis.py`, `CoSimulationAnalysis` takes `(cosim_settings, models=None)`, unlike the runner's `(model, parameters)` call. Add an explicit adapter, coupled-case validation, and one small example with interface convergence and transferred-field checks.

**Probe:** run a minimal two-solver coupling through MCP, verify interface data exchange, and ensure an invalid transfer configuration is diagnosed.

### Adaptive remeshing — L

Structured mesh generation and mesh conversion already exist. Add an optional MeshingApplication workflow around `applications/MeshingApplication/python_scripts/mmg_process.py`, with metric settings, boundary preservation, field transfer, and before/after mesh summaries. Check MMG availability before offering the workflow.

**Probe:** refine a localized region while preserving named boundaries and a known transferred field; compare the solution with a uniformly refined case.

### Optimization and reduced-order studies — XL

Treat these as separate experiments after sweeps and response extraction work. Kratos provides `applications/OptimizationApplication/python_scripts/optimization_analysis.py` and `applications/RomApplication/python_scripts/rom_manager.py`. The former already has a `(model, parameters)` constructor compatible with generic dispatch; the missing work is scaffolding, validation, response histories, and examples. ROM needs a separate lifecycle for training, validation, and online runs.

**Probe:** first reproduce a tiny optimization reference with its objective history; separately train a small ROM and report error on held-out parameters against full-order results. Store the training inputs and basis provenance.

## Non-goals and decisions taken

Recorded so they are not re-proposed as gaps.

- **No Kratos or PyVista imports in the MCP server process.** Keep native execution in workers to protect stdio transport and contain native crashes.
- **No replacement graphical editor.** FlowGraph import/export already provides the visual editing route; improve interoperability when a concrete gap arises.
- **No duplicate solver or process registry.** Extend existing runtime discovery and source parsing, using the configured Kratos build and source as authority.
- **No promise that every Kratos application is supported by a template.** The generic analysis-class entry point remains available; curated workflows need capability checks and numerical reference cases.

---

## Suggested sequencing

1. Deliver checkpoint/resume and time-history comparisons.
2. Build sweeps on isolated runs; add local MPI support with lifecycle tests.
3. Introduce CoSimulation and remeshing as independently optional workflows.
4. Evaluate optimization and ROM once reproducibility and quantitative comparisons are established. These are exploratory directions, not release commitments.
