"""Simulation execution tools backed by the job manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio

from .. import jobs, logparse
from .scaffold import validate_case_files


def register(mcp) -> None:

    @mcp.tool()
    async def run_simulation(
        case_dir: str,
        parameters_file: str = "ProjectParameters.json",
        analysis_type: str | None = None,
        analysis_class: str | None = None,
        wait_seconds: float = 0,
    ) -> dict[str, Any]:
        """Start a Kratos simulation as a background job and return its
        job_id. The analysis class is taken from the 'analysis_stage' key in
        the parameters, or inferred from solver_type; override with
        analysis_type (structural/fluid/thermal/potential_flow) or
        analysis_class ('module.path:ClassName'). If wait_seconds > 0, poll
        up to that long and return the final status if the job finishes in
        time. Track progress with job_status/job_logs."""
        try:
            meta = await anyio.to_thread.run_sync(lambda: jobs.start(
                case_dir, parameters_file, analysis_type, analysis_class))
        except (RuntimeError, FileNotFoundError) as exc:
            return {"error": str(exc)}

        waited = 0.0
        status = jobs.status(meta.job_id)
        while wait_seconds > 0 and waited < wait_seconds \
                and status["state"] not in jobs.TERMINAL_STATES:
            await anyio.sleep(min(2.0, wait_seconds - waited))
            waited += 2.0
            status = jobs.status(meta.job_id)
        if status["state"] == "failed":
            status["log_tail"] = jobs.logs(meta.job_id, tail=30)
        return status

    @mcp.tool()
    async def validate_case(
        case_dir: str, parameters_file: str = "ProjectParameters.json"
    ) -> dict[str, Any]:
        """Dry-run check of a case directory without running the time loop:
        JSON validity, required keys, mesh and materials files exist and
        parse, model part references match the mesh, and the solver settings
        validate against Kratos defaults."""
        return await anyio.to_thread.run_sync(
            lambda: validate_case_files(case_dir, parameters_file, deep=True))

    @mcp.tool()
    def job_status(job_id: str) -> dict[str, Any]:
        """Get the state of a simulation job (queued/running/succeeded/
        failed/cancelled), elapsed time, and current step/time parsed from
        its log."""
        try:
            status = jobs.status(job_id)
        except KeyError as exc:
            return {"error": str(exc)}
        try:
            log_text = jobs.log_path(job_id).read_text(errors="replace")
            status["progress"] = logparse.progress(log_text)
        except OSError:
            pass
        return status

    @mcp.tool()
    def job_list(state: str | None = None) -> list[dict[str, Any]]:
        """List all known simulation jobs, optionally filtered by state
        (queued, running, succeeded, failed, cancelled)."""
        return jobs.list_jobs(state)

    @mcp.tool()
    def job_logs(job_id: str, tail: int = 100, grep: str | None = None) -> dict[str, Any]:
        """Return the last 'tail' lines of a job's simulation log, optionally
        only lines containing the 'grep' substring (case-insensitive)."""
        try:
            return {"job_id": job_id, "log": jobs.logs(job_id, tail=tail, grep=grep)}
        except KeyError as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def job_cancel(job_id: str) -> dict[str, Any]:
        """Cancel a running simulation job (SIGTERM, escalating to SIGKILL
        after a grace period)."""
        try:
            return jobs.cancel(job_id)
        except KeyError as exc:
            return {"error": str(exc)}
