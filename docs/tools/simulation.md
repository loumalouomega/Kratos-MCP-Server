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
