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

`worker.py` and `runner.py` are the ONLY modules that import Kratos, and they
only run inside subprocesses with the env vars injected.

The same class of rule covers pyvista/VTK: its OpenGL init can abort the
process on headless/misconfigured systems, so `tools/visualize.py` spawns
`render_worker.py` (the ONLY module that imports pyvista) as a plain
subprocess — no Kratos env needed — with the same `--result-file` JSON
convention. When no display is available it starts a private Xvfb (if
installed) via `-displayfd`.

## Commands

- `uv sync` — install deps (`mcp`, `meshio`, `numpy`; dev: pytest, ipykernel,
  nbclient, nbformat)
- `uv sync --extra viz` — adds pyvista + imageio for `results_render`/
  `results_animate` (optional; without it those tools return an install hint)
- `uv run kratos-mcp` — run the server (stdio)
- `uv run pytest -m "not kratos"` — unit tests, no Kratos needed
- `uv run pytest -m kratos` — integration tests against the real build
  (cantilever + thermal bar end-to-end with physics assertions)
- `uv run python tests/smoke_client.py` — scripted stdio MCP client smoke test
- `npm run docs:dev` / `npm run docs:build` — VitePress docs

## Layout

- `src/kratos_mcp/server.py` — FastMCP instance + main()
- `src/kratos_mcp/tools/` — one module per tool category (`environment`,
  `scaffold`, `mesh`, `simulation`, `postprocess`, `visualize`, `resources`,
  `prompts`), each exposing `register(mcp)`; `register_all` in `__init__.py`
- `src/kratos_mcp/render_worker.py` — pyvista/VTK rendering, subprocess-only
  (see above)
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
- `src/kratos_mcp/examples/cantilever/` — real `mesh.mdpa` +
  ProjectParameters.json + Materials.json files (a coarse 4x1 case), read
  verbatim by the `kratos://examples/cantilever` resource in
  `tools/resources.py`, not rendered from `templates/` at request time. If
  `structural_static`'s template changes, regenerate these files by hand
  and re-verify the numbers with a real run (recipe in
  `tests/test_examples.py` and the cantilever-beam.md tutorial).
  `thermal-bar` stays dynamically rendered via `_example_bundle()`.
- `notebooks/cantilever.ipynb` — MCP *client* notebook (uses `mcp.client.stdio`
  directly, not the server code) walking through most tools/resources/prompts
  against the real cantilever case; all outputs are baked in from a real run,
  not placeholders. Regenerate after a workflow-affecting change by rebuilding
  it with `nbformat` and re-executing with `nbclient`/`jupyter nbconvert
  --execute` against a kernel that has this project's `.venv` (`uv sync --extra
  viz --group dev`, then `python -m ipykernel install --user --name <name>`)
  — same "re-verify with a real run" rule as the cantilever example resource.
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
- `Kernel.Get*VariableNames()` return one newline-separated string, not lists.
- `KM.Parameters` tolerates `//` comments; plain `json.loads` on Kratos test
  files may fail.
- Kratos resolves mdpa/materials paths relative to the CWD → `runner.py`
  chdirs into the case directory.

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
