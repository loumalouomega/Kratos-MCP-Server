# Getting started

The Kratos MCP Server connects AI assistants (Claude Code, Claude Desktop, or
any [MCP](https://modelcontextprotocol.io) client) to
[Kratos Multiphysics](https://github.com/KratosMultiphysics/Kratos), the
open-source multiphysics finite element framework.

Once connected, an assistant can carry a simulation through its whole
lifecycle:

1. **Check the environment** — `kratos_check_installation` reports the Kratos
   version and which applications are compiled.
2. **Create a mesh** — `mdpa_create_structured_mesh` writes a line, rectangle
   or box mesh with named boundary regions (`left`, `right`, `xmin`, ...).
3. **Scaffold the case** — `create_project` renders ProjectParameters.json and
   Materials.json from a template (`structural_static`, `thermal_transient`,
   `fluid_transient`, ...).
4. **Add boundary conditions** — `add_boundary_condition` inserts fixes,
   loads, fluxes or inlet/outlet conditions into the parameters file.
5. **Validate** — `validate_case` catches missing files, bad model part
   references and invalid solver settings before anything runs.
6. **Run** — `run_simulation` starts a background job; `job_status`,
   `job_logs` and `job_cancel` manage it.
7. **Inspect results** — `results_list`, `results_summary`, `results_probe`
   and `results_convergence` read the VTK output and the solver log.

## Five-minute setup

```bash
git clone <this-repo> Kratos-MCP-Server
cd Kratos-MCP-Server
uv sync

# Point at a Kratos checkout that contains a compiled bin/Release build
export KRATOS_ROOT=/path/to/Kratos

claude mcp add kratos -e KRATOS_ROOT=$KRATOS_ROOT -- \
    uv --directory "$PWD" run kratos-mcp
```

Then, in Claude Code:

> Check the Kratos installation, then set up and run a cantilever plate
> (1 m × 0.2 m, fixed on the left, 1 MN/m downward line load on the right)
> and report the tip deflection.

The assistant will chain the tools above and answer with the deflection —
for the setup in the [cantilever tutorial](/tutorials/cantilever-beam) it is
about **0.43 mm downward**, within a few percent of beam theory.

## What you need

| Requirement | Notes |
| --- | --- |
| Python ≥ 3.10 + [uv](https://docs.astral.sh/uv/) | server runtime |
| Compiled Kratos build | `bin/Release` inside `KRATOS_ROOT`; see [Installation](/guide/installation) |
| Kratos applications | StructuralMechanics, ConvectionDiffusion, FluidDynamics, LinearSolvers cover all templates |
| Node 18+ | only for building this documentation |

## Where to go next

- [Installation](/guide/installation) — environment variables, MKL, verifying
  the setup
- [Connecting a client](/guide/connecting) — Claude Code, Claude Desktop and
  generic MCP configuration
- [Tool reference](/tools/) — every tool with parameters and examples
- [Tutorials](/tutorials/cantilever-beam) — worked structural and thermal
  examples
