"""Interactive Kratos session worker: a long-lived process that keeps a live
Model + AnalysisStage in memory and is driven one request at a time.

Runs INSIDE the Kratos environment as `python -m kratos_mcp.session_worker
--case-dir DIR`, spawned by sessions.SessionManager. This is one of the only
modules that import Kratos (with worker.py and runner.py).

Protocol (JSON lines):
  * requests arrive on stdin: {"id": int, "op": str, "args": {...}}
  * responses leave on a private copy of the ORIGINAL stdout pipe:
    {"id": int, "ok": true, "result": ...} or
    {"id": int, "ok": false, "error": str, "traceback": str}
  * the first response (id 0) is the "ready" handshake carrying the start
    summary, or a fatal error.
File descriptors 1 and 2 are re-pointed at the session log right at startup,
so the Kratos banner and solver output can never corrupt the response channel.

The Kratos-free helpers at the top (stats, JSON pointers) are importable
without Kratos so they can be unit tested anywhere."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import math
import os
import sys
import threading
import time
import traceback
from typing import Any

from .runner import ANALYSIS_CLASSES, detect_analysis_type, find_analysis_class_in_module

DEFAULT_IDLE_TIMEOUT = 3600.0


# --------------------------------------------------------------------------
# Kratos-free helpers
# --------------------------------------------------------------------------

def norm(value: Any) -> float:
    """Euclidean norm of a scalar or vector value."""
    if isinstance(value, (int, float)):
        return abs(float(value))
    return math.sqrt(sum(float(v) * float(v) for v in value))


def to_plain(value: Any) -> Any:
    """Convert Kratos value types (Array3, Vector, Matrix, ...) to JSON-able data."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    try:
        n = len(value)
    except TypeError:
        return str(value)
    try:
        return [to_plain(value[i]) for i in range(n)]
    except Exception:
        pass
    try:  # Matrix
        rows, cols = value.Size1(), value.Size2()
        return [[to_plain(value[i, j]) for j in range(cols)] for i in range(rows)]
    except Exception:
        return str(value)


def summarize(items: list[tuple[int, Any, Any]]) -> dict[str, Any]:
    """min/max/mean of (entity_id, location, value) triples.

    Vector values are compared by norm; the extremes report the entity id,
    its location (node coordinates, or None) and the raw value."""
    if not items:
        return {"count": 0}
    norms = [norm(v) for _, _, v in items]
    imax = max(range(len(items)), key=lambda i: norms[i])
    imin = min(range(len(items)), key=lambda i: norms[i])

    def entry(i: int) -> dict[str, Any]:
        eid, loc, val = items[i]
        out = {"id": eid, "value": to_plain(val), "magnitude": norms[i]}
        if loc is not None:
            out["coordinates"] = to_plain(loc)
        return out

    first = items[0][2]
    out: dict[str, Any] = {
        "count": len(items),
        "kind": "scalar" if isinstance(first, (int, float)) else "vector",
        "min": norms[imin],
        "max": norms[imax],
        "mean": sum(norms) / len(norms),
        "argmax": entry(imax),
        "argmin": entry(imin),
    }
    if not isinstance(first, (int, float)):
        n = len(first)
        out["component_max"] = [max(float(v[c]) for _, _, v in items) for c in range(n)]
        out["component_min"] = [min(float(v[c]) for _, _, v in items) for c in range(n)]
    return out


def json_pointer_get(document: Any, pointer: str) -> Any:
    """RFC 6901 lookup; '' or '/' returns the whole document."""
    if pointer in ("", "/"):
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    node = document
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(key)]
        elif isinstance(node, dict):
            if key not in node:
                raise KeyError(f"'{key}' not found at {pointer!r}")
            node = node[key]
        else:
            raise KeyError(f"cannot descend into a scalar at {pointer!r}")
    return node


def exec_allowed(environ: dict[str, str] | None = None) -> bool:
    return (environ if environ is not None else os.environ).get(
        "KRATOS_MCP_ALLOW_EXEC", "") in ("1", "true", "yes")


# --------------------------------------------------------------------------
# The stateful session
# --------------------------------------------------------------------------

class KratosSession:
    """Holds the Kratos Model and the initialized AnalysisStage between calls."""

    def __init__(self, KM, case_dir: str, parameters_file: str,
                 analysis_type: str | None, analysis_class: str | None) -> None:
        self.KM = KM
        self.case_dir = case_dir
        self.parameters_file = parameters_file
        with open(parameters_file) as f:
            text = f.read()
        self.parameters = KM.Parameters(text)
        if self.parameters.Has("orchestrator") and self.parameters.Has("stages"):
            raise ValueError("Multi-stage cases cannot be stepped interactively; "
                             "use run_simulation.")
        if self.parameters.Has("solver_settings") and \
                self.parameters["solver_settings"].Has("coupling_sequence"):
            raise ValueError("CoSimulation (coupled-solver) cases cannot be stepped "
                             "interactively; use run_simulation.")
        if self.parameters.Has("solver_settings") and \
                self.parameters["solver_settings"].Has("solver_type") and \
                self.parameters["solver_settings"]["solver_type"].GetString().lower().startswith("partitioned"):
            raise ValueError("Partitioned FSI cases cannot be stepped interactively; "
                             "use run_simulation.")
        if '"mmg_process"' in text:
            raise ValueError("Cases with an mmg_process (remeshing) cannot be stepped "
                             "interactively; use run_simulation.")
        problem_data =self.parameters["problem_data"] if self.parameters.Has("problem_data") else None
        if problem_data is not None and problem_data.Has("parallel_type") \
                and problem_data["parallel_type"].GetString() != "OpenMP":
            raise ValueError("Interactive sessions are serial/OpenMP only "
                             "(problem_data.parallel_type must be 'OpenMP').")

        if analysis_class:
            module_path, _, class_name = analysis_class.partition(":")
            if not class_name:
                raise ValueError("analysis_class must be 'module.path:ClassName'")
            cls = getattr(importlib.import_module(module_path), class_name)
        elif analysis_type is None and self.parameters.Has("analysis_stage"):
            cls = find_analysis_class_in_module(self.parameters["analysis_stage"].GetString())
        else:
            module_path, class_name = ANALYSIS_CLASSES[
                analysis_type or detect_analysis_type(self.parameters)]
            cls = getattr(importlib.import_module(module_path), class_name)

        self.model = KM.Model()
        self.stage = cls(self.model, self.parameters)
        self.analysis_class = f"{cls.__module__}:{cls.__name__}"
        self.stage.Initialize()
        self.steps_taken = 0
        self.finalized = False
        self.last_time = self._time()
        self.main_model_part_name = self._solver_model_part_name()

    # -- small accessors ---------------------------------------------------
    def _time(self) -> float:
        return float(getattr(self.stage, "time", 0.0))

    def _solver_model_part_name(self) -> str:
        try:
            return self.parameters["solver_settings"]["model_part_name"].GetString()
        except Exception:
            names = self.model.GetModelPartNames()
            return names[0] if names else ""

    def model_part(self, name: str | None):
        name = name or self.main_model_part_name
        try:
            return self.model[name]
        except Exception as exc:
            raise KeyError(f"Model part '{name}' not found; available: "
                           f"{list(self.model.GetModelPartNames())}") from exc

    def variable(self, name: str):
        KM = self.KM
        try:
            return KM.KratosGlobals.GetVariable(name)
        except Exception as exc:
            raise KeyError(f"Unknown variable '{name}'") from exc

    def _end_time(self) -> float | None:
        try:
            return float(self.stage.end_time)
        except Exception:
            return None

    # -- ops -----------------------------------------------------------------
    def status(self, args: dict) -> dict[str, Any]:
        mp = self.model_part(None) if self.main_model_part_name else None
        info = mp.ProcessInfo if mp is not None else None
        return {
            "analysis_class": self.analysis_class,
            "time": self._time(),
            "end_time": self._end_time(),
            "step": int(info[self.KM.STEP]) if info is not None else self.steps_taken,
            "steps_taken": self.steps_taken,
            "finished": self.finished(),
            "finalized": self.finalized,
            "model_part": self.main_model_part_name,
            "nodes": mp.NumberOfNodes() if mp is not None else None,
            "elements": mp.NumberOfElements() if mp is not None else None,
            "conditions": mp.NumberOfConditions() if mp is not None else None,
            "model_parts": list(self.model.GetModelPartNames()),
        }

    def finished(self) -> bool:
        try:
            return not self.stage.KeepAdvancingSolutionLoop()
        except Exception:
            return False

    def get_parameters(self, args: dict) -> Any:
        document = json.loads(self.parameters.WriteJsonString())
        return json_pointer_get(document, args.get("path", ""))

    def advance(self, args: dict) -> dict[str, Any]:
        if self.finalized:
            raise RuntimeError("Session is finalized; start a new one.")
        steps = int(args.get("steps", 1))
        until_time = args.get("until_time")
        output = bool(args.get("output", True))
        if steps < 1 and until_time is None:
            raise ValueError("steps must be >= 1 (or give until_time)")
        records: list[dict[str, Any]] = []
        stage = self.stage
        limit = steps if until_time is None else max(steps, 10 ** 9)
        stop_reason = "steps_completed"
        while len(records) < limit:
            if not stage.KeepAdvancingSolutionLoop():
                stop_reason = "end_time_reached"
                break
            if until_time is not None and self._time() >= float(until_time) - 1e-12:
                stop_reason = "until_time_reached"
                break
            t0 = time.time()
            stage.time = stage._AdvanceTime()
            stage.InitializeSolutionStep()
            solve = getattr(stage, "SolveSolutionStep", None) or stage._GetSolver().SolveSolutionStep
            converged = solve()
            stage.FinalizeSolutionStep()
            if output:
                stage.OutputSolutionStep()
            self.steps_taken += 1
            mp = self.model_part(None)
            records.append({
                "step": int(mp.ProcessInfo[self.KM.STEP]),
                "time": self._time(),
                "converged": None if converged is None else bool(converged),
                "wall_seconds": round(time.time() - t0, 4),
            })
        if until_time is None and len(records) == limit:
            stop_reason = "steps_completed"
        if not stage.KeepAdvancingSolutionLoop() and stop_reason == "steps_completed":
            stop_reason = "end_time_reached"
        self.last_time = self._time()
        return {
            "advanced": len(records),
            "time": self._time(),
            "end_time": self._end_time(),
            "finished": self.finished(),
            "stop_reason": stop_reason,
            "history": records,
        }

    def model_info(self, args: dict) -> dict[str, Any]:
        KM = self.KM
        root = self.model_part(args.get("model_part"))

        def describe(mp, depth: int) -> dict[str, Any]:
            out = {
                "name": mp.Name,
                "nodes": mp.NumberOfNodes(),
                "elements": mp.NumberOfElements(),
                "conditions": mp.NumberOfConditions(),
            }
            if depth > 0:
                out["sub_model_parts"] = [describe(sub, depth - 1)
                                          for sub in mp.SubModelParts]
            return out

        info = root.ProcessInfo
        result = describe(root, int(args.get("depth", 4)))
        result["process_info"] = {
            "TIME": info[KM.TIME], "STEP": info[KM.STEP],
            "DELTA_TIME": info[KM.DELTA_TIME],
            "DOMAIN_SIZE": info[KM.DOMAIN_SIZE],
        }
        result["buffer_size"] = root.GetBufferSize()
        result["available_model_parts"] = list(self.model.GetModelPartNames())
        return result

    def field_summary(self, args: dict) -> dict[str, Any]:
        variable = self.variable(args["variable"])
        mp = self.model_part(args.get("model_part"))
        location = args.get("location", "nodes")
        items: list[tuple[int, Any, Any]] = []
        if location == "nodes":
            for node in mp.Nodes:
                items.append((node.Id, [node.X, node.Y, node.Z],
                              _plain_value(node.GetSolutionStepValue(variable))))
        elif location == "elements":
            info = mp.ProcessInfo
            for element in mp.Elements:
                values = element.CalculateOnIntegrationPoints(variable, info)
                if not len(values):
                    continue
                vals = [_plain_value(v) for v in values]
                best = max(vals, key=norm)  # worst-case Gauss point
                items.append((element.Id, None, best))
        else:
            raise ValueError("location must be 'nodes' or 'elements'")
        out = summarize(items)
        out.update({"variable": args["variable"], "model_part": mp.Name,
                    "location": location, "time": self._time()})
        return out

    def inspect_node(self, args: dict) -> dict[str, Any]:
        mp = self.model_part(args.get("model_part"))
        node_id = int(args["node_id"])
        try:
            node = mp.GetNode(node_id)
        except Exception as exc:
            raise KeyError(f"Node {node_id} not found in '{mp.Name}'") from exc
        names = args.get("variables") or _default_node_variables(mp)
        values, fixed = {}, {}
        for name in names:
            var = self.variable(name)
            try:
                values[name] = to_plain(node.GetSolutionStepValue(var))
            except Exception as exc:
                values[name] = f"unavailable ({type(exc).__name__})"
            try:
                if isinstance(var, self.KM.Array1DVariable3):
                    fixed[name] = [bool(node.IsFixed(self.variable(f"{name}_{c}")))
                                   for c in "XYZ"]
                else:
                    fixed[name] = bool(node.IsFixed(var))
            except Exception:
                pass
        return {"id": node_id, "coordinates": [node.X, node.Y, node.Z],
                "initial_coordinates": [node.X0, node.Y0, node.Z0],
                "values": values, "fixed": fixed, "time": self._time()}

    def inspect_element(self, args: dict) -> dict[str, Any]:
        mp = self.model_part(args.get("model_part"))
        element_id = int(args["element_id"])
        try:
            element = mp.GetElement(element_id)
        except Exception as exc:
            raise KeyError(f"Element {element_id} not found in '{mp.Name}'") from exc
        out: dict[str, Any] = {
            "id": element_id,
            "node_ids": [n.Id for n in element.GetNodes()],
            "properties_id": element.Properties.Id,
            "active": bool(element.IsActive()),
            "time": self._time(),
        }
        values = {}
        for name in args.get("variables") or []:
            var = self.variable(name)
            try:
                values[name] = to_plain(element.CalculateOnIntegrationPoints(var, mp.ProcessInfo))
            except Exception as exc:
                values[name] = f"unavailable ({type(exc).__name__}: {exc})"
        out["values"] = values
        return out

    def set_nodal_values(self, args: dict) -> dict[str, Any]:
        """Assign a value to a nodal variable, optionally fixing/freeing the DOF.

        Vector variables (e.g. DISPLACEMENT) take a list of 3 floats (or a
        scalar broadcast to all components); fix applies to every component,
        or per component when given as a list of booleans."""
        if self.finalized:
            raise RuntimeError("Session is finalized; start a new one.")
        KM = self.KM
        variable = self.variable(args["variable"])
        mp = self.model_part(args.get("model_part"))
        value = args["value"]
        fix = args.get("fix")
        node_ids = args.get("node_ids")
        nodes = [mp.GetNode(int(i)) for i in node_ids] if node_ids else list(mp.Nodes)
        is_vector = isinstance(variable, KM.Array1DVariable3)
        if is_vector:
            if isinstance(value, (int, float)):
                value = [float(value)] * 3
            if len(value) != 3:
                raise ValueError(f"{args['variable']} takes 3 components, got {len(value)}")
            value = KM.Array3([float(v) for v in value])
            components = [self.variable(f"{args['variable']}_{c}") for c in "XYZ"]
            if isinstance(fix, list):
                if len(fix) != 3:
                    raise ValueError("per-component fix needs 3 booleans")
                fixes = [bool(f) for f in fix]
            else:
                fixes = [fix] * 3 if fix is not None else [None] * 3
        elif isinstance(value, list):
            raise ValueError(f"{args['variable']} is a scalar variable; give a number")
        else:
            value = float(value)
            components, fixes = [variable], [fix if not isinstance(fix, list) else bool(fix[0])]
        for node in nodes:
            node.SetSolutionStepValue(variable, 0, value)
            for component, flag in zip(components, fixes):
                if flag is True:
                    node.Fix(component)
                elif flag is False:
                    node.Free(component)
        return {"variable": args["variable"], "model_part": mp.Name,
                "nodes_changed": len(nodes), "value": to_plain(args["value"]),
                "fix": fix}

    def exec_python(self, args: dict) -> dict[str, Any]:
        if not exec_allowed():
            raise PermissionError(
                "session_exec_python is disabled. Set KRATOS_MCP_ALLOW_EXEC=1 in "
                "the MCP server's environment to enable it.")
        namespace = {"KM": self.KM, "model": self.model, "stage": self.stage,
                     "session": self, "parameters": self.parameters, "result": None}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(compile(args["code"], "<session_exec_python>", "exec"), namespace)
        return {"result": to_plain(namespace.get("result")), "stdout": buffer.getvalue()}

    def finalize(self, args: dict) -> dict[str, Any]:
        if not self.finalized:
            self.stage.Finalize()
            self.finalized = True
        return {"finalized": True, "time": self._time(), "steps_taken": self.steps_taken}


def _plain_value(value: Any) -> Any:
    """Scalars stay floats; vector-like Kratos values become lists."""
    if isinstance(value, (int, float)):
        return float(value)
    return to_plain(value)


def _default_node_variables(mp) -> list[str]:
    names = []
    for name in ("DISPLACEMENT", "REACTION", "VELOCITY", "PRESSURE", "TEMPERATURE"):
        try:
            if mp.HasNodalSolutionStepVariable(_global_variable(name)):
                names.append(name)
        except Exception:
            continue
    return names


def _global_variable(name: str):
    import KratosMultiphysics as KM
    return KM.KratosGlobals.GetVariable(name)


# --------------------------------------------------------------------------
# Process entry point
# --------------------------------------------------------------------------

def _redirect_to_log(log_path: str | None):
    """Return a text stream on a private copy of the original stdout pipe,
    then point fds 1 and 2 (and sys.stdout/stderr) at the session log."""
    resp = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    if log_path:
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        sys.stdout = os.fdopen(1, "w", buffering=1, closefd=False)
        sys.stderr = os.fdopen(2, "w", buffering=1, closefd=False)
    return resp


def _send(resp, payload: dict[str, Any]) -> None:
    resp.write(json.dumps(payload) + "\n")
    resp.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--parameters", default="ProjectParameters.json")
    parser.add_argument("--analysis-type", default=None, choices=sorted(ANALYSIS_CLASSES))
    parser.add_argument("--analysis-class", default=None)
    parser.add_argument("--log-file", default=None)
    ns = parser.parse_args()

    resp = _redirect_to_log(ns.log_file)
    idle_timeout = float(os.environ.get("KRATOS_MCP_SESSION_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT))

    try:
        os.chdir(ns.case_dir)  # Kratos resolves mdpa/materials paths relative to cwd
        import KratosMultiphysics as KM
        session = KratosSession(KM, ns.case_dir, ns.parameters,
                                ns.analysis_type, ns.analysis_class)
        ready = session.status({})
    except BaseException as exc:  # noqa: BLE001 - report any startup failure
        _send(resp, {"id": 0, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                     "traceback": traceback.format_exc()})
        return 1
    _send(resp, {"id": 0, "ok": True, "result": ready})

    ops = {
        "status": session.status,
        "get_parameters": session.get_parameters,
        "advance": session.advance,
        "model_info": session.model_info,
        "field_summary": session.field_summary,
        "inspect_node": session.inspect_node,
        "inspect_element": session.inspect_element,
        "set_nodal_values": session.set_nodal_values,
        "exec": session.exec_python,
        "finalize": session.finalize,
    }

    last_activity = [time.time()]
    busy = threading.Event()

    def watchdog() -> None:
        while True:
            time.sleep(5.0)
            if not busy.is_set() and time.time() - last_activity[0] > idle_timeout:
                print(f"[session_worker] idle for {idle_timeout:.0f}s; exiting", file=sys.stderr)
                os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()

    for line in sys.stdin:  # EOF == the server went away
        line = line.strip()
        if not line:
            continue
        busy.set()
        req_id = -1
        try:
            request = json.loads(line)
            req_id = int(request["id"])
            op = request["op"]
            if op not in ops:
                raise KeyError(f"Unknown session op '{op}'")
            result = ops[op](request.get("args") or {})
            _send(resp, {"id": req_id, "ok": True, "result": to_plain(result)})
            if op == "finalize":
                return 0
        except BaseException as exc:  # noqa: BLE001
            _send(resp, {"id": req_id, "ok": False,
                         "error": f"{type(exc).__name__}: {exc}",
                         "traceback": traceback.format_exc()})
        finally:
            busy.clear()
            last_activity[0] = time.time()
    return 0


if __name__ == "__main__":
    sys.exit(main())
