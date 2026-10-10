# Kratos MCP Server

[![CI](https://github.com/loumalouomega/Kratos-MCP-Server/actions/workflows/ci.yml/badge.svg)](https://github.com/loumalouomega/Kratos-MCP-Server/actions/workflows/ci.yml)
[![Deploy docs](https://github.com/loumalouomega/Kratos-MCP-Server/actions/workflows/docs.yml/badge.svg)](https://loumalouomega.github.io/Kratos-MCP-Server/)
[![Release](https://github.com/loumalouomega/Kratos-MCP-Server/actions/workflows/release.yml/badge.svg)](https://github.com/loumalouomega/Kratos-MCP-Server/releases)
[![PyPI](https://img.shields.io/pypi/v/kratos-mcp-server.svg)](https://pypi.org/project/kratos-mcp-server/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/github/license/loumalouomega/Kratos-MCP-Server)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants drive
[Kratos Multiphysics](https://github.com/KratosMultiphysics/Kratos) finite
element simulations end to end:

- **Introspect** the installation: applications, elements, conditions,
  constitutive laws, variables, solvers and processes and their default
  parameters (process defaults are parsed from the Kratos source, no build
  needed).
- **Scaffold** simulation cases from templates: structural
  (static/dynamic/modal), thermal (transient/stationary), fluid (monolithic
  or fractional-step incompressible) and potential flow — plus **multi-stage
  orchestrated** cases that chain analyses and **CoSimulation** cases where
  several solvers exchange interface data inside each step (weak/strong
  Gauss–Seidel or Jacobi coupling). Curated material and linear-solver
  presets included. ProjectParameters.json, Materials.json and structured MDPA
  meshes with named boundary regions.
- **Study** parameter sweeps and structured mesh convergence with isolated
  variants, a bounded background queue, and physical-location probe responses
  ([tools](docs/tools/studies.md), [tutorial](docs/tutorials/studies.md)).
- **Run** simulations (single- or multi-stage) as managed background jobs
  (status, live logs, progress, cancel) that survive server restarts, with
  optional immutable input snapshots, reproducible reruns, checkpoint resume
  for isolated serial single-stage jobs, and local MPI launch (`mpi_ranks`,
  `omp_threads`) with per-rank logs and whole-group cancellation.
- **Drive interactively**: open a live *session* (a Kratos `Model` +
  `AnalysisStage` kept in memory), advance it a few steps at a time, inspect
  fields, nodes and elements between steps, pin or change nodal values, and
  continue — the direct, stateful route into Kratos' Python layer next to the
  batch-job route. Optional raw Python access behind `KRATOS_MCP_ALLOW_EXEC=1`
  ([tools](docs/tools/sessions.md), [tutorial](docs/tutorials/interactive-session.md)).
- **Post-process** VTK results: summaries, point probes, convergence
  analysis, physical-time histories, CSV export, and reference comparisons.
- **Preview** results without ParaView: PNG screenshots and GIF animations
  (deformed shapes, field contours) rendered with pyvista and shown inline
  in the conversation — optional `viz` extra.
- **Interoperate**: explain an existing ProjectParameters.json, and convert
  cases to/from the [Kratos FlowGraph](https://github.com/KratosMultiphysics/Flowgraph)
  visual node editor (lossless round-trip).

65 tools, 17 resources (12 worked examples) and 6 guided prompts. See the full documentation in
[`docs/`](docs/) (VitePress).

## Quick start

A local Kratos build is **not required** — the server can pip-install
Kratos itself on first use.

**Once published to PyPI**, no clone needed — `uvx` fetches and runs it:

```bash
claude mcp add kratos -- uvx kratos-mcp-server
```

**From a local checkout** (current state, before the first PyPI release):

```bash
git clone https://github.com/loumalouomega/Kratos-MCP-Server
cd Kratos-MCP-Server
uv sync
claude mcp add kratos -- uv --directory "$PWD" run kratos-mcp
```

Then ask your assistant something like:

> Check the Kratos installation — install it if it's missing — then set up
> a cantilever plate 1 m × 0.2 m fixed on the left with a 1 MN/m downward
> load on the right edge, run it, and report the tip deflection.

The assistant calls `kratos_install` the first time and reuses it afterwards.
If you already have a compiled Kratos checkout, skip that and point
`KRATOS_ROOT` at it instead (`-e KRATOS_ROOT=/path/to/Kratos` on the `claude
mcp add` line) — see [Installation](docs/guide/installation.md).

## Notebooks

Seven notebooks drive the server interactively as an MCP *client* — no AI
assistant involved — each touching most of the relevant tools, resources
and prompts in one sitting. Run any with `uv sync --extra viz --group
dev` (adds `ipykernel` + pyvista) and open it in Jupyter/VS Code against
that `.venv`.

- [`notebooks/cantilever.ipynb`](notebooks/cantilever.ipynb): the structural
  cantilever case — installation introspection, mesh generation,
  scaffolding, a background job you poll while it runs, VTK
  post-processing, a rendered PNG and an animated GIF, and cancelling a job
  in flight.
- [`notebooks/naca_airfoil.ipynb`](notebooks/naca_airfoil.ipynb): a NACA0012
  airfoil in incompressible laminar flow — reuses a real ~21k-node airfoil
  mesh from Kratos's own examples repo (simplified physics; see the
  notebook/tutorial for what and why), computes lift/drag by summing
  `REACTION` over the airfoil surface, and renders/animates the pressure
  field cropped to the airfoil with the newer `crop_bounds` option.
- [`notebooks/fluid_cavity.ipynb`](notebooks/fluid_cavity.ipynb): the
  lid-driven cavity CFD benchmark — introspect the fluid solver, run the
  shipped example, probe the velocity field, and render an inline PNG and GIF
  of the recirculating vortex.
- [`notebooks/materials.ipynb`](notebooks/materials.ipynb): the materials
  surface — material & linear-solver presets, process-defaults introspection,
  and a single-element von Mises plasticity run showing the elastic→plastic
  transition.
- [`notebooks/multistage.ipynb`](notebooks/multistage.ipynb): multi-stage
  orchestration — `create_multistage_project` chaining two load steps,
  `explain_project_parameters`, and a lossless round-trip to/from a
  [Kratos FlowGraph](https://github.com/KratosMultiphysics/Flowgraph) node graph.
- [`notebooks/interactive_session.ipynb`](notebooks/interactive_session.ipynb):
  interactive sessions — step a load-ramped cantilever two steps at a time,
  inspect fields and nodes between steps, pin a DOF mid-run, peek into the
  live Kratos objects, and verify the stepped result equals a batch run.
- [`notebooks/interactive_fluid.ipynb`](notebooks/interactive_fluid.ipynb):
  stepping the lid-driven cavity in chunks and watching the flow approach
  steady state through live field summaries.
- [`docs/tutorials/potential-flow.md`](docs/tutorials/potential-flow.md): a
  runnable NACA0012 perturbation potential-flow benchmark with upstream
  reference values.

## Requirements

- Python ≥ 3.10, [uv](https://docs.astral.sh/uv/)
- Kratos Multiphysics, either pip-installed via `kratos_install`
  (Linux/Windows x86_64 only — no macOS wheels) or a compiled build (tested
  with 10.4, StructuralMechanics / ConvectionDiffusion / FluidDynamics /
  LinearSolvers applications)
- Optional, for `results_render`/`results_animate`: the `viz` extra
  (`uv sync --extra viz` or `pip install 'kratos-mcp-server[viz]'`) and a
  working OpenGL context (on headless machines: Xvfb or OSMesa VTK wheels —
  see [docs/tools/visualization.md](docs/tools/visualization.md))

## Architecture in one paragraph

Kratos is never imported in the server process — it prints a banner on import
(which would corrupt the stdio JSON-RPC stream) and can abort the process on
solver errors. Short operations run in a **worker** subprocess that returns
JSON through a result file; simulations run **detached** with per-job
directories under `~/.kratos-mcp/jobs/`; **interactive sessions** keep a live
`AnalysisStage` in a long-lived worker driven over JSON lines. See
[docs/guide/architecture.md](docs/guide/architecture.md).

## Development

See the [roadmap](docs/roadmap.md) for planned work and the
[changelog](CHANGELOG.md) for release history.

```bash
uv run pytest -m "not kratos"   # unit tests (no Kratos needed)
uv run pytest -m kratos          # integration tests against the real build
npm install && npm run docs:dev  # documentation site
```

## License

[MIT](LICENSE)
