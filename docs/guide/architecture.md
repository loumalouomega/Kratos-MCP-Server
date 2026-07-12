# Architecture

## The core constraint

Kratos cannot run inside the MCP server process, for three reasons:

1. **stdout is sacred.** MCP over stdio uses stdout for JSON-RPC framing, and
   `import KratosMultiphysics` prints a multi-line ASCII banner to stdout.
   One import would corrupt the protocol stream.
2. **Crashes.** Kratos is a C++ core; a solver bug or bad input can abort the
   whole process (segfault). The server must survive that.
3. **Environment.** A build-tree Kratos needs `PYTHONPATH` and
   `LD_LIBRARY_PATH` set *before* the interpreter starts — too late for a
   process that is already running.

So the server process never imports Kratos. Two subprocess mechanisms cover
all Kratos access:

```
┌──────────────────────────────┐
│  MCP client (Claude, ...)    │
└──────────────┬───────────────┘
               │ stdio JSON-RPC
┌──────────────▼───────────────┐
│  kratos-mcp server (FastMCP) │   never imports Kratos
│  tools/ · mdpa.py · jobs.py  │
└─────┬───────────────────┬────┘
      │ bridge.run_op()   │ jobs.start()
      │ (sync, seconds)   │ (detached, minutes-hours)
┌─────▼─────────┐   ┌─────▼─────────┐
│  worker.py    │   │  runner.py    │   both import Kratos,
│  introspection│   │  AnalysisStage│   env vars injected
│  validation   │   │  .Run()       │
└───────────────┘   └───────────────┘
```

## Getting Kratos itself

`kratos_env.resolve()` finds Kratos in one of three ways, in order: an
explicit `KRATOS_PYTHONPATH`/`KRATOS_LIBS` override, a `KRATOS_ROOT`
checkout with a compiled `bin/Release`, or a **pip-installed**
`KratosMultiphysics` importable by the server's own interpreter — probed in
a subprocess so a missing package fails safely instead of raising in the
server process. That third path is what `kratos_env.pip_install()` and the
`kratos_install` tool populate: they run `pip install` (never Kratos itself)
directly in the server process — safe, since pip does not import
Kratos — installing the official `KratosMultiphysics` / `Kratos<AppName>` /
`KratosMultiphysics-all` wheels (Linux/Windows x86_64 only). Once installed,
the very next `resolve()` call picks it up automatically; no restart needed.
A local build always takes priority when both are present.

## The bridge (short operations)

`bridge.run_op(op, args)` spawns `python -m kratos_mcp.worker` with the Kratos
environment injected and a JSON request file. The worker writes its result to
a **result file** — never stdout — so banners and solver chatter cannot
corrupt it; stdout/stderr are captured and attached to error messages.

Ops: `check`, `list_applications`, `list_variables`, `has_constitutive_laws`,
`get_solver_defaults`, `validate_parameters`, `read_mdpa_deep`.

Because each spawn costs one to a few seconds, results of build-dependent ops
are cached in `~/.kratos-mcp/cache/`, keyed by `(op, args, build
fingerprint)` — editing or rebuilding Kratos invalidates the cache
automatically.

## Jobs (simulations)

`jobs.start()` launches `python -m kratos_mcp.runner --case-dir ...` fully
detached (own session), with everything persisted under
`~/.kratos-mcp/jobs/<job_id>/`:

```
20260712-101530-a1b2c3/
├── meta.json     # state machine: queued → running → succeeded|failed|cancelled
└── stdout.log    # combined solver output
```

- **Status** is recomputed from the process return code, or — after a server
  restart, when the child handle is gone — from pid liveness plus the
  AnalysisStage end banner in the log.
- **Progress** (`current_step`, `current_time`) is parsed from the
  `STEP:`/`TIME:` lines AnalysisStage prints each step.
- **Cancel** sends SIGTERM to the job's process group and escalates to
  SIGKILL after a grace period.

The runner picks the analysis class from the `analysis_stage` key in
ProjectParameters.json (the convention used by Kratos itself and by all our
templates), from an explicit `analysis_type`/`analysis_class` argument, or by
inferring it from `solver_type`.

## Hybrid introspection

What the tools report comes from two sources:

- **Runtime** (authoritative for *your build*): version, compiled
  applications, variables, constitutive-law existence, solver default
  parameters — via the bridge.
- **Source parsing**: element/condition/constitutive-law catalogs are parsed
  from `KRATOS_REGISTER_*` macros in the C++ sources, because Kratos has no
  runtime listing for them. Entries are flagged `compiled: true/false` by
  cross-referencing the compiled application list.

## Pure-Python MDPA layer

`mdpa.py` parses, writes, validates and generates `.mdpa` meshes without
Kratos, so mesh tools work even where no build is available and unit tests
run anywhere. `mdpa_validate(deep=true)` additionally round-trips the file
through the real `ModelPartIO` in a worker.

## Templates as data

Case templates live in `src/kratos_mcp/templates/` as JSON files with
`{{placeholder}}` markers plus a `registry.json` describing defaults,
required applications and solver modules. Substitution is typed: a quoted
`"{{key}}"` becomes the JSON encoding of the value (numbers stay numbers,
arrays stay arrays); a bare `{{key}}` inside a longer string is textual.
