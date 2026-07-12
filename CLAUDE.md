# Kratos MCP Server

MCP (Model Context Protocol) server exposing Kratos Multiphysics to AI
assistants: installation introspection, case scaffolding (ProjectParameters /
Materials / MDPA meshes), simulation execution as background jobs, and VTK
post-processing. Python, `mcp` SDK (FastMCP), stdio transport.

## Environment

Kratos is NOT pip-installed; it lives in a compiled build tree resolved by
`src/kratos_mcp/kratos_env.py`:

- `KRATOS_ROOT` (default `/home/vicente/src/Kratos`) → uses
  `$KRATOS_ROOT/bin/Release` as `PYTHONPATH` and `$KRATOS_ROOT/bin/Release/libs`
  as `LD_LIBRARY_PATH`.
- Overrides: `KRATOS_PYTHONPATH`, `KRATOS_LIBS`, `KRATOS_SOURCE` (source tree
  for macro parsing), `KRATOS_EXTRA_LIBS` (extra lib dirs; MKL under
  `/opt/intel/oneapi/mkl/latest/lib` is auto-detected — LinearSolversApplication
  needs `libmkl_rt.so.2`).
- Manual incantation for ad-hoc Kratos scripts:
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

`worker.py` and `runner.py` are the ONLY modules that import Kratos, and they
only run inside subprocesses with the env vars injected.

## Commands

- `uv sync` — install deps (`mcp`, `meshio`, `numpy`; dev: pytest)
- `uv run kratos-mcp` — run the server (stdio)
- `uv run pytest -m "not kratos"` — unit tests, no Kratos needed
- `uv run pytest -m kratos` — integration tests against the real build
  (cantilever + thermal bar end-to-end with physics assertions)
- `uv run python tests/smoke_client.py` — scripted stdio MCP client smoke test
- `npm run docs:dev` / `npm run docs:build` — VitePress docs

## Layout

- `src/kratos_mcp/server.py` — FastMCP instance + main()
- `src/kratos_mcp/tools/` — one module per tool category (`environment`,
  `scaffold`, `mesh`, `simulation`, `postprocess`, `resources`, `prompts`),
  each exposing `register(mcp)`; `register_all` in `__init__.py`
- `src/kratos_mcp/mdpa.py` — pure-Python MDPA parse/write/inspect/validate +
  structured mesh generators (line/rectangle/box with named boundary parts)
- `src/kratos_mcp/source_catalog.py` — parses `KRATOS_REGISTER_ELEMENT/
  CONDITION/CONSTITUTIVE_LAW` macros from the Kratos C++ sources (there is no
  runtime registry for these)
- `src/kratos_mcp/templates/` — case templates as data: `registry.json`
  (metadata + placeholder defaults) + `<name>/ProjectParameters.json.tpl` +
  `Materials.json.tpl`. Substitution: quoted `"{{key}}"` → JSON-typed value,
  bare `{{key}}` → text.
- `src/kratos_mcp/logparse.py` — step/convergence extraction from job logs

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
- `Kernel.Get*VariableNames()` return one newline-separated string, not lists.
- `KM.Parameters` tolerates `//` comments; plain `json.loads` on Kratos test
  files may fail.
- Kratos resolves mdpa/materials paths relative to the CWD → `runner.py`
  chdirs into the case directory.

## Conventions

- All server logging to stderr (stdout is the MCP transport).
- Tools return plain dicts; errors as `{"error": ...}` rather than raising
  (bridge failures include worker stdout/stderr tails for diagnosis).
- Blocking work inside async tools goes through `anyio.to_thread.run_sync`.
- Tool inputs/outputs use absolute paths.
- Reference cases used for templates/tests:
  `applications/StructuralMechanicsApplication/tests/LinearTruss2D/2D2N/` and
  `applications/ConvectionDiffusionApplication/tests/basic_conv_diffusion_test/`
  in the Kratos tree.

## Keep docs in sync

Every time you change code in this repo, check whether doc/, README.md, and this file need updating too — and update them if they do. Treat doc drift as part of the change, not a follow-up.
