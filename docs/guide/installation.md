# Installation

## 1. Install the server

```bash
git clone <this-repo> Kratos-MCP-Server
cd Kratos-MCP-Server
uv sync
```

`uv sync` creates a virtual environment with the runtime dependencies
(`mcp`, `meshio`, `numpy`) and the dev tools (`pytest`).

## 2. Provide a Kratos build

The server does **not** bundle Kratos. It needs either:

- **A compiled checkout** (the usual case): a Kratos source tree with a
  `bin/Release` (or `bin/FullDebug` / `bin/Debug`) directory containing the
  `KratosMultiphysics` Python package and its `libs/`. Follow the
  [Kratos build instructions](https://github.com/KratosMultiphysics/Kratos/blob/master/INSTALL.md),
  or
- **A pip installation**: `pip install KratosMultiphysics-all`. The server
  detects this automatically when no build tree is found.

## 3. Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `KRATOS_ROOT` | `/home/vicente/src/Kratos` | Kratos checkout root; `bin/Release` is derived from it |
| `KRATOS_PYTHONPATH` | derived | Explicit directory containing `KratosMultiphysics/` (overrides `KRATOS_ROOT`) |
| `KRATOS_LIBS` | `$KRATOS_PYTHONPATH/libs` | Explicit shared-library directory |
| `KRATOS_SOURCE` | `$KRATOS_ROOT` | Source tree used for element/condition catalogs |
| `KRATOS_EXTRA_LIBS` | auto-detected | Extra `LD_LIBRARY_PATH` entries (e.g. MKL) |
| `KRATOS_MCP_HOME` | `~/.kratos-mcp` | Server state: jobs, caches |

The server injects `PYTHONPATH` and `LD_LIBRARY_PATH` into every Kratos
subprocess — you do not need to export them yourself.

### Intel MKL

If Kratos was built with `USE_EIGEN_MKL` (common for the
LinearSolversApplication), its solvers need `libmkl_rt.so.2` at runtime. The
server auto-detects MKL under `/opt/intel/oneapi/mkl/latest/lib`; for other
locations set:

```bash
export KRATOS_EXTRA_LIBS=/path/to/mkl/lib
```

## 4. Verify

```bash
# The worker subprocess must be able to import Kratos:
KRATOS_ROOT=/path/to/Kratos uv run python -c \
  "from kratos_mcp import bridge; print(bridge.run_op('check', use_cache=False)['version'])"

# Full test suite against the build:
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
