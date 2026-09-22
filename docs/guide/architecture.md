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

The same isolation pattern covers pyvista: `render_worker.py` (spawned by
the `results_render`/`results_animate` tools) is the only code that imports
VTK, because VTK's OpenGL setup can abort the process on headless or
misconfigured systems. It doesn't need the Kratos environment — just the
server's own interpreter — so it is a plain subprocess without the bridge's
env injection or caching.

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
├── manifest.json # command, build fingerprint, environment, and input hashes
├── snapshot/     # immutable inputs for isolated jobs
├── execution/    # isolated solver working directory (isolated jobs only)
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

`jobs.start(..., isolate=True)` copies the case into the job directory before
launching Kratos. It records hashes for the copied inputs and keeps the
snapshot separate from the execution directory, so a solver can write output
without changing the inputs used by `job_rerun`. External files must be mapped
explicitly to relative snapshot destinations; absolute paths and paths that
escape the isolated case are rejected. Existing callers keep the original
in-place behavior when isolation is omitted.

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


### Checkpoints and physical-time output indexes

For serial single-stage runs, `runner.py` observes native restart and VTK
process output calls and atomically publishes `checkpoint-index.json` and
`result-index.json` in the execution directory after successful writes. Time
and step come from the process model part, never the filename. Checkpoint
records include SHA-256 hashes and remain discoverable after retention deletes
the binary file. Indexes are reset when a new run starts in a working directory.

`checkpoints.py` configures restart output and verifies isolated source jobs
before preparing a new snapshot with a dedicated `restart_input/` directory.
`result_series.py` reads meshes one time pair at a time through meshio/numpy;
MCP wrappers run this work in worker threads. Neither module imports Kratos or
PyVista. Resume is distinct from rerun: resume restores serialized model state,
whereas rerun repeats the preserved starting inputs (including a checkpoint if
that job was itself resumed).

## Persistent studies

`studies.py` prepares a frozen base and statically validates all parameter or
structured-mesh variants before creating launchable child jobs. Study state lives
under `KRATOS_MCP_HOME/studies/<id>/`; child snapshots, manifests and executions
remain under the ordinary jobs directory. Inputs, overrides, mesh summaries,
response histories and errors remain independently inspectable.

`study_coordinator.py` runs detached from the MCP server and bounds active children
per study. Each prepared job has a durable identity before launch. A detached
`job_supervisor.py` claims that identity under a POSIX file lock, runs the existing
Kratos runner in its process group, and records its actual exit status. Ownership
locks and atomic metadata writes prevent duplicate execution after coordinator
recovery. Neither module imports Kratos or PyVista.

Cancellation uses durable requests and process-group termination, including jobs
that are still queued. Coordinator recovery verifies hashes and build identity,
adopts active children, and schedules remaining work. A claimed job whose
supervisor dies is failed and its remaining process group is terminated, rather
than being silently repeated. Response extraction uses the existing meshio/numpy
time-history code. See [study semantics](/tools/studies) for limits and defaults.
