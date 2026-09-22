"""MCP interfaces for persistent parameter and mesh studies."""
from __future__ import annotations

from typing import Any

import anyio

from .. import studies


async def _call(function, *args, **kwargs):
    try:
        return await anyio.to_thread.run_sync(lambda: function(*args, **kwargs))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError) as exc:
        return {'error': str(exc)}


def register(mcp) -> None:
    @mcp.tool()
    async def study_start(case_dir: str, specification: dict[str, Any], responses: list[dict[str, Any]],
                          max_concurrency: int = 1, parameters_file: str = 'ProjectParameters.json',
                          analysis_type: str | None = None, analysis_class: str | None = None,
                          external_inputs: dict[str, str] | None = None) -> dict[str, Any]:
        """Prepare all isolated variants, then start a detached bounded study.

        specification: {kind:'parameter', axes:[{file:'Materials.json',
        pointer:'/properties/0/Material/Variables/YOUNG_MODULUS', values:[1e11,2e11]}],
        combination:'product'|'zip'} or {kind:'mesh', mesh:{kind:'rectangle',
        size:[1,0.2]}, divisions:[[4,1],[8,2],[16,4]]}. Optional atol/rtol/time_atol
        control mesh-response comparisons. Responses: [{name:'tip',
        variable:'DISPLACEMENT', point:[1,0,0], component:1, reduction:'final'}].
        Optional response association: point/cell, series, max_distance;
        component may be 'magnitude'; reduction may be final/min/max.
        Serial single-stage POSIX jobs only. Progress survives server exit.
        """
        return await _call(studies.start, case_dir, specification, responses, max_concurrency,
                           parameters_file, analysis_type, analysis_class, external_inputs)

    @mcp.tool()
    async def study_status(study_id: str) -> dict[str, Any]:
        """Return durable study progress, child jobs, provenance, and response errors."""
        return await _call(studies.status, study_id)

    @mcp.tool()
    async def study_list(state: str | None = None) -> list[dict[str, Any]] | dict[str, Any]:
        """List studies, optionally filtering by their study-level state."""
        return await _call(studies.list_studies, state)

    @mcp.tool()
    async def study_cancel(study_id: str) -> dict[str, Any]:
        """Stop pending launches and cancel active process groups; preserve results."""
        return await _call(studies.cancel, study_id)

    @mcp.tool()
    async def study_resume(study_id: str) -> dict[str, Any]:
        """Verify and resume an interrupted coordinator, adopting existing child jobs."""
        return await _call(studies.resume, study_id)

    @mcp.tool()
    async def study_results(study_id: str, csv_file: str | None = None) -> dict[str, Any]:
        """Get partial/complete probe histories, scalar responses and mesh differences.

        Optional CSV uses exclusive creation and never overwrites an existing file.
        """
        return await _call(studies.results, study_id, csv_file)
