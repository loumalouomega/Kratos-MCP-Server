# Simulation & jobs

Simulations run as **detached background jobs** — the server stays
responsive, a solver crash cannot take it down, and jobs survive server
restarts. State lives in `~/.kratos-mcp/jobs/<job_id>/`.

## run_simulation

*needs Kratos*

Start a simulation job.

| Parameter | Type | Description |
| --- | --- | --- |
| `case_dir` | string | directory containing the case |
| `parameters_file` | string | default `ProjectParameters.json` |
| `analysis_type` | string? | `structural` / `fluid` / `thermal` / `potential_flow` override |
| `analysis_class` | string? | fully qualified `module.path:ClassName` override |
| `wait_seconds` | number | poll up to this long and return the final status if the job finishes in time (default 0 = return immediately) |
| `isolate` | boolean | copy inputs to a private snapshot and execution directory before launch (default `false`) |
| `external_inputs` | object? | when isolating, map absolute source files to relative destinations inside the snapshot |

The analysis class is normally resolved from the `analysis_stage` key that
our templates write into ProjectParameters.json, falling back to inference
from `solver_type`. **Multi-stage** cases (an `orchestrator` + `stages`
ProjectParameters, e.g. from
[`create_multistage_project`](/tools/scaffolding#create-multistage-project))
are detected automatically and driven through Kratos' `SequentialOrchestrator`
— same tool, same job lifecycle.

**Returns**: the job status (below). For quick cases pass
`wait_seconds: 60`–`120` and get the terminal state in one call; failed jobs
include a `log_tail`.

```json
{ "job_id": "20260712-101530-a1b2c3", "state": "succeeded", "returncode": 0, "elapsed_seconds": 2.0 }
```

Isolated jobs return the execution directory in `case_dir` and store
`manifest.json`, an immutable `snapshot/`, and the solver working copy under
the job directory. The manifest records input SHA-256 hashes, the Kratos build
fingerprint, launch command, and relevant environment settings. External mesh
or material files must be supplied explicitly, for example
`external_inputs={"/data/mesh.mdpa": "inputs/mesh.mdpa"}`. Output paths must
remain relative to the isolated case.

## validate_case

*needs Kratos for the deep part*

Dry-run check of a case directory — everything
[`validate_project_parameters`](/tools/scaffolding#validate-project-parameters)
does, run from the case root. Call it before `run_simulation`; it catches the
common failure modes in seconds instead of after a failed run.

## job_status

| Parameter | Type |
| --- | --- |
| `job_id` | string |

**Returns**: `state` (`queued` / `running` / `succeeded` / `failed` /
`cancelled`), `returncode`, `elapsed_seconds`, and `progress` parsed from the
log: `current_step`, `current_time`, `num_steps_seen`, `errors_detected`.

## job_list

List all known jobs, optionally filtered by `state`.

## job_logs

| Parameter | Type | Description |
| --- | --- | --- |
| `job_id` | string | |
| `tail` | int | last N lines (default 100) |
| `grep` | string? | case-insensitive substring filter |

The complete live log is also available as the resource
`kratos://jobs/{job_id}/log`.

## job_cancel

Cancel a running job: SIGTERM to the job's process group, escalating to
SIGKILL after a 5 s grace period. Cancelling a finished job is a no-op and
returns its final state.

## job_rerun

Rerun an isolated job from its preserved snapshot after verifying every input
hash and the Kratos build fingerprint. The new job receives its own execution
directory and manifest. In-place jobs cannot be rerun because they do not keep
an immutable input snapshot.

## configure_checkpoints

Configure native restart output before calling `run_simulation(isolate=true)`.
Pass `case_dir`, a positive `frequency`, and optionally `control_type` (`time`
or `step`, default `time`), `max_files_to_keep` (`-1` for all, or a positive
integer), and `parameters_file`. Step frequencies must be integers. Repeated
calls update the matching model-part process. Output stays inside the case;
the default directory is `checkpoints`.

## job_checkpoints

Pass `job_id` to list completed checkpoints in numeric label order. Each record
includes `path`, `file`, `label`, physical `time`, `step`, `size`, `sha256`, and
`available`. Deleted checkpoints remain visible with `available=false` after
retention cleanup. An interrupted write is never published as a new checkpoint.

## job_resume

Pass `job_id` and an explicit `checkpoint` path from `job_checkpoints`.
The source must be a terminal, isolated, serial, single-stage job. The optional
`end_time` defaults to the original end time and must exceed checkpoint time;
`wait_seconds` behaves as in `run_simulation`.

The server verifies snapshot hashes, the build fingerprint, and checkpoint
checksum, then copies inputs and the checkpoint into a **new** job. Its snapshot
contains `restart_input/`, separate from subsequent checkpoint output. The
source remains unchanged. Metadata and the manifest retain `resume_of` and the
selected checkpoint's provenance. Resumed jobs support `job_rerun` and further
`job_resume` calls.

```python
configure_checkpoints(case_dir="/tmp/beam", frequency=10, control_type="step")
run_simulation(case_dir="/tmp/beam", isolate=True)
job_checkpoints(job_id="<source-job>")
job_cancel(job_id="<source-job>")
# Refresh the list after cancellation, since retention may have removed files.
job_checkpoints(job_id="<source-job>")
job_resume(job_id="<source-job>", checkpoint="<available-absolute-path>",
           end_time=1.0, wait_seconds=60)
```

Native serial structural dynamics is covered by a numerical restart test.
Other solvers/processes must themselves support Kratos restart semantics;
missing applications and deserialization failures appear in the new job's log.
External checkpoint imports, non-isolated source jobs, MPI checkpoints, and
orchestrator stage checkpoints are not supported by this managed workflow.

## Independent studies

Use [parameter and mesh studies](/tools/studies) to run an isolated, bounded
queue of independent variants and collect responses. Study children have normal
job IDs and support the job tools on this page. `job_rerun` creates a separate
job; it does not replace a study's recorded child result.
