"""Interactive session tools: drive a live Kratos AnalysisStage step by step.

Unlike run_simulation (a detached batch job that runs to completion), a
session keeps the Model and the initialized AnalysisStage in a long-lived
worker process, so an assistant can advance a few steps, inspect fields and
nodes, change nodal values, and continue. Backed by sessions.py; the Kratos
code lives only in session_worker.py."""

from __future__ import annotations

from typing import Any

import anyio

from .. import sessions


def register(mcp) -> None:

    async def _call(session_id: str, op: str, args: dict[str, Any] | None = None,
                    timeout: float = 120.0) -> dict[str, Any]:
        try:
            return await anyio.to_thread.run_sync(
                lambda: sessions.request(session_id, op, args, timeout=timeout))
        except KeyError as exc:
            return {"error": str(exc.args[0]) if exc.args else str(exc)}
        except sessions.SessionError as exc:
            out: dict[str, Any] = {"error": str(exc)}
            if getattr(exc, "busy", False):
                out["busy"] = True
            return out

    @mcp.tool()
    async def session_start(
        case_dir: str,
        parameters_file: str = "ProjectParameters.json",
        analysis_type: str | None = None,
        analysis_class: str | None = None,
        isolate: bool = False,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Open an interactive Kratos session: load the case, build the
        AnalysisStage and run Initialize(), then return a session_id and the
        initial state (time 0, node/element counts, model parts). Nothing is
        solved until session_advance. Unlike run_simulation this keeps the
        live model in memory so you can step, inspect and modify it.

        Single-stage, serial/OpenMP cases only (multi-stage and MPI cases are
        rejected: use run_simulation). isolate=True works on a private copy of
        the case so the original files and outputs stay untouched. Sessions do
        not survive a server restart; close them with session_close. At most
        KRATOS_MCP_MAX_SESSIONS (default 4) can be open at once."""
        try:
            return await anyio.to_thread.run_sync(lambda: sessions.start(
                case_dir, parameters_file, analysis_type, analysis_class,
                isolate, timeout))
        except sessions.SessionError as exc:
            return {"error": str(exc)}

    @mcp.tool()
    def session_list(state: str | None = None) -> list[dict[str, Any]]:
        """List interactive sessions (newest first), optionally filtered by
        state: starting/ready/closed/dead/lost. 'lost' means the session
        belonged to a previous server process."""
        return sessions.list_sessions(state)

    @mcp.tool()
    def session_status(session_id: str, log_tail: int = 0) -> dict[str, Any]:
        """State of a session: ready/closed/dead/lost, whether the worker is
        busy with a request, recorded step count, and (for dead sessions) why
        it died with the log tail. Set log_tail > 0 to include the last N
        lines of the session's solver log. Use session_model_info for the
        live simulation time/step."""
        try:
            out = sessions.status(session_id)
            if log_tail > 0:
                out["log"] = sessions.logs(session_id, log_tail)
            return out
        except KeyError as exc:
            return {"error": str(exc.args[0]) if exc.args else str(exc)}

    @mcp.tool()
    async def session_get_parameters(session_id: str, path: str = "") -> dict[str, Any]:
        """Read the live ProjectParameters of a session (after defaults were
        applied). path is an optional JSON Pointer such as
        '/solver_settings/time_stepping' or '/processes/loads_process_list/0';
        empty returns everything."""
        out = await _call(session_id, "get_parameters", {"path": path})
        return out if "error" in out else {"path": path, "parameters": out}

    @mcp.tool()
    async def session_advance(
        session_id: str,
        steps: int = 1,
        until_time: float | None = None,
        output: bool = True,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Advance the simulation by `steps` solution steps (or until the
        model time reaches `until_time`), running the same sequence as
        AnalysisStage.RunSolutionLoop: AdvanceInTime, InitializeSolutionStep,
        SolveSolutionStep, FinalizeSolutionStep and, if output=True, the
        configured output processes. Stops early at the case's end_time.

        Returns the new time, whether the run is finished, why it stopped
        (steps_completed / until_time_reached / end_time_reached) and a
        per-step history (step, time, converged, wall_seconds). If the call
        exceeds `timeout` seconds it returns busy=True and the worker keeps
        running; poll session_status until busy is false."""
        out = await _call(session_id, "advance",
                          {"steps": steps, "until_time": until_time, "output": output},
                          timeout=timeout)
        if "error" not in out:
            await anyio.to_thread.run_sync(
                lambda: sessions.record_history(session_id, out.get("history", [])))
        return out

    @mcp.tool()
    async def session_model_info(session_id: str, model_part: str | None = None,
                                 depth: int = 4) -> dict[str, Any]:
        """Live model structure: node/element/condition counts for a model part
        (default: the solver's main model part) and its sub model parts down to
        `depth`, plus ProcessInfo TIME/STEP/DELTA_TIME/DOMAIN_SIZE and the
        buffer size. Use it to discover valid model_part names."""
        return await _call(session_id, "model_info",
                           {"model_part": model_part, "depth": depth})

    @mcp.tool()
    async def session_field_summary(
        session_id: str,
        variable: str,
        model_part: str | None = None,
        location: str = "nodes",
    ) -> dict[str, Any]:
        """Summarise a field at the CURRENT time: min/max/mean (vectors by
        magnitude, with component-wise min/max), and the node/element where the
        maximum and minimum occur (with coordinates for nodes).

        location='nodes' reads nodal solution-step variables (DISPLACEMENT,
        REACTION, VELOCITY, PRESSURE, TEMPERATURE, ...). location='elements'
        evaluates an element result at the integration points and reports the
        worst Gauss point per element (e.g. VON_MISES_STRESS, TEMPERATURE
        gradients, CAUCHY_STRESS_VECTOR)."""
        return await _call(session_id, "field_summary",
                           {"variable": variable, "model_part": model_part,
                            "location": location})

    @mcp.tool()
    async def session_inspect_node(
        session_id: str,
        node_id: int,
        variables: list[str] | None = None,
        model_part: str | None = None,
    ) -> dict[str, Any]:
        """Inspect one node: current and initial coordinates, the values of
        `variables` (default: DISPLACEMENT/REACTION/VELOCITY/PRESSURE/
        TEMPERATURE as available) and whether each DOF is fixed."""
        return await _call(session_id, "inspect_node",
                           {"node_id": node_id, "variables": variables,
                            "model_part": model_part})

    @mcp.tool()
    async def session_inspect_element(
        session_id: str,
        element_id: int,
        variables: list[str] | None = None,
        model_part: str | None = None,
    ) -> dict[str, Any]:
        """Inspect one element: its node ids, properties id, active flag, and
        the integration-point values of `variables` (e.g. ['VON_MISES_STRESS',
        'GREEN_LAGRANGE_STRAIN_VECTOR'])."""
        return await _call(session_id, "inspect_element",
                           {"element_id": element_id, "variables": variables,
                            "model_part": model_part})

    @mcp.tool()
    async def session_set_nodal_values(
        session_id: str,
        variable: str,
        value: Any,
        node_ids: list[int] | None = None,
        model_part: str | None = None,
        fix: bool | list[bool] | None = None,
    ) -> dict[str, Any]:
        """Assign a nodal solution-step value (a number, or [x, y, z] for vector
        variables such as DISPLACEMENT or VELOCITY) to node_ids, or to every
        node of model_part when node_ids is omitted. fix=True/False fixes/frees
        the DOF (a list of 3 booleans sets each vector component); omit it to
        leave fixity unchanged. Takes effect from the next session_advance, so
        you can add a support, pin a temperature or perturb the state
        mid-run. Writes into the live model only: files on disk are unchanged."""
        return await _call(session_id, "set_nodal_values",
                           {"variable": variable, "value": value, "node_ids": node_ids,
                            "model_part": model_part, "fix": fix})

    @mcp.tool()
    async def session_exec_python(session_id: str, code: str) -> dict[str, Any]:
        """Run Python inside the session's Kratos process for anything the
        typed tools do not cover. The namespace holds KM (KratosMultiphysics),
        model, stage (the AnalysisStage), session, parameters; assign a
        JSON-serialisable value to `result` to get it back, and print() output
        is returned as stdout. Example: result = model['Structure'].GetNode(75).X.

        DISABLED unless the MCP server was started with KRATOS_MCP_ALLOW_EXEC=1,
        because it executes arbitrary code as the server's user."""
        if not sessions.exec_enabled():
            return {"error": "session_exec_python is disabled. Start the MCP server "
                             "with KRATOS_MCP_ALLOW_EXEC=1 to enable it (it runs "
                             "arbitrary code as your user)."}
        return await _call(session_id, "exec", {"code": code})

    @mcp.tool()
    async def session_close(session_id: str, finalize: bool = True) -> dict[str, Any]:
        """Close a session. With finalize=True (default) the AnalysisStage's
        Finalize() runs first so output processes flush (VTK/GiD files, restart
        files); then the worker process group is terminated. Safe to call on an
        already closed or dead session."""
        try:
            return await anyio.to_thread.run_sync(
                lambda: sessions.close(session_id, finalize=finalize))
        except KeyError as exc:
            return {"error": str(exc.args[0]) if exc.args else str(exc)}
