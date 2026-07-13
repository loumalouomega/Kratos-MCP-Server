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
  constitutive laws, variables, solvers and their default parameters.
- **Scaffold** simulation cases from templates: structural
  (static/dynamic/modal), thermal (transient/stationary) and fluid
  (transient incompressible) — ProjectParameters.json, Materials.json and
  structured MDPA meshes with named boundary regions.
- **Run** simulations as managed background jobs (status, live logs,
  progress, cancel) that survive server restarts.
- **Post-process** VTK results: summaries, point probes, convergence
  analysis.
- **Preview** results without ParaView: PNG screenshots and GIF animations
  (deformed shapes, field contours) rendered with pyvista and shown inline
  in the conversation — optional `viz` extra.

33 tools, 7 resources and 4 guided prompts. See the full documentation in
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

## Notebook

[`notebooks/cantilever.ipynb`](notebooks/cantilever.ipynb) drives the same
cantilever case interactively as an MCP *client* — no AI assistant involved —
touching most of the server's tools, resources and prompts in one sitting:
installation introspection, mesh generation, scaffolding, a background job
you poll while it runs, VTK post-processing, a rendered PNG and an animated
GIF, and cancelling a job in flight. Run it with `uv sync --extra viz --group
dev` (adds `ipykernel` + pyvista) and open it in Jupyter/VS Code against that
`.venv`.

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
directories under `~/.kratos-mcp/jobs/`. See
[docs/guide/architecture.md](docs/guide/architecture.md).

## Development

```bash
uv run pytest -m "not kratos"   # unit tests (no Kratos needed)
uv run pytest -m kratos          # integration tests against the real build
npm install && npm run docs:dev  # documentation site
```

## License

[MIT](LICENSE)
