# Installation

## 1. Install the server

```bash
git clone <this-repo> Kratos-MCP-Server
cd Kratos-MCP-Server
uv sync
```

`uv sync` creates a virtual environment with the runtime dependencies
(`mcp`, `meshio`, `numpy`) and the dev tools (`pytest`).

## 2. Get Kratos

A local compiled build is **not required**. There are two ways to get
Kratos, and you can mix them across machines:

- **Let the assistant install it** (easiest): once the server is running,
  ask it to call the `kratos_install` tool — it pip-installs Kratos
  straight into the server's own Python environment, no manual steps.
  Official wheels exist for **Linux and Windows x86_64 only** (there is no
  Kratos wheel for macOS — use a local build there instead). You can also
  do this by hand: `pip install KratosMultiphysics-all` (or `pip install
  KratosMultiphysics KratosStructuralMechanicsApplication ...` for just the
  applications you need — see the [tool reference](/tools/environment#kratos-install)
  for the exact package-naming rule). The server detects a pip-installed
  Kratos automatically, no environment variables needed.
- **Point at a compiled checkout**: a Kratos source tree with a
  `bin/Release` (or `bin/FullDebug` / `bin/Debug`) directory containing the
  `KratosMultiphysics` Python package and its `libs/`. Useful for custom
  builds, applications without PyPI wheels, or macOS. Follow the
  [Kratos build instructions](https://github.com/KratosMultiphysics/Kratos/blob/master/INSTALL.md),
  then set `KRATOS_ROOT` to point at it (see below).

When both are present, an explicit `KRATOS_ROOT`/`KRATOS_PYTHONPATH` build
takes priority over a pip installation.

## 3. Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `KRATOS_ROOT` | `/home/vicente/src/Kratos` | Kratos checkout root; `bin/Release` is derived from it. Unset it (or point it at a nonexistent path) to force the pip-installed Kratos to be used instead |
| `KRATOS_PYTHONPATH` | derived | Explicit directory containing `KratosMultiphysics/` (overrides `KRATOS_ROOT`) |
| `KRATOS_LIBS` | `$KRATOS_PYTHONPATH/libs` | Explicit shared-library directory |
| `KRATOS_SOURCE` | `$KRATOS_ROOT` | Source tree used for element/condition catalogs (only meaningful with a local build) |
| `KRATOS_EXTRA_LIBS` | auto-detected | Extra `LD_LIBRARY_PATH` entries (e.g. MKL) |
| `KRATOS_MCP_HOME` | `~/.kratos-mcp` | Server state: jobs, caches |

None of these are required if you're happy pip-installing Kratos via
`kratos_install` — the server injects `PYTHONPATH` and `LD_LIBRARY_PATH`
into every Kratos subprocess only when a local build is configured.

### Intel MKL

If Kratos was built with `USE_EIGEN_MKL` (common for the
LinearSolversApplication), its solvers need `libmkl_rt.so.2` at runtime. The
server auto-detects MKL under `/opt/intel/oneapi/mkl/latest/lib`; for other
locations set:

```bash
export KRATOS_EXTRA_LIBS=/path/to/mkl/lib
```

This does not apply to a pip-installed Kratos — those wheels bundle their
own dependencies.

## 4. Verify

```bash
# The worker subprocess must be able to import Kratos:
uv run python -c \
  "from kratos_mcp import bridge; print(bridge.run_op('check', use_cache=False)['version'])"

# Full test suite against whichever Kratos is resolved:
uv run pytest -m kratos
```

The integration suite scaffolds and runs real simulations (a structural
cantilever and a thermal bar) and asserts the physics of the results.

## 5. Documentation site (optional)

```bash
npm install
npm run docs:dev     # live-reload at http://localhost:5173
npm run docs:build   # static site in docs/.vitepress/dist
```
