# Kratos MCP Server

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

30 tools, 7 resources and 4 guided prompts. See the full documentation in
[`docs/`](docs/) (VitePress).

## Quick start

```bash
# 1. Install dependencies
uv sync

# 2. Point the server at your Kratos build (contains bin/Release)
export KRATOS_ROOT=/path/to/Kratos

# 3. Register with Claude Code
claude mcp add kratos -e KRATOS_ROOT=$KRATOS_ROOT -- \
    uv --directory /path/to/Kratos-MCP-Server run kratos-mcp
```

Then ask your assistant something like:

> Set up a cantilever plate 1 m × 0.2 m fixed on the left with a 1 MN/m
> downward load on the right edge, run it, and report the tip deflection.

## Requirements

- Python ≥ 3.10, [uv](https://docs.astral.sh/uv/)
- A compiled Kratos Multiphysics build (tested with 10.4,
  StructuralMechanics / ConvectionDiffusion / FluidDynamics /
  LinearSolvers applications)

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

MIT
