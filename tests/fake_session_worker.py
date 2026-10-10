"""A Kratos-free stand-in for kratos_mcp.session_worker used by test_sessions.py.

It speaks the same JSON-lines protocol (and redirects fds 1/2 to the session
log exactly like the real worker) but implements a tiny counter "simulation"
plus ops that provoke the failure modes the manager must handle."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from kratos_mcp.session_worker import _redirect_to_log, _send


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--parameters", default="ProjectParameters.json")
    parser.add_argument("--analysis-type", default=None)
    parser.add_argument("--analysis-class", default=None)
    parser.add_argument("--log-file", default=None)
    ns = parser.parse_args()
    resp = _redirect_to_log(ns.log_file)
    os.chdir(ns.case_dir)
    print("FAKE BANNER that must never reach the response channel")
    if os.path.exists("fail_startup"):
        _send(resp, {"id": 0, "ok": False, "error": "RuntimeError: boom",
                     "traceback": "tb"})
        return 1
    if os.path.exists("hang_startup"):
        time.sleep(60)
    state = {"step": 0, "time": 0.0}
    _send(resp, {"id": 0, "ok": True, "result": {
        "analysis_class": "fake:Analysis", "time": 0.0, "end_time": 1.0, "step": 0}})
    for line in sys.stdin:
        request = json.loads(line)
        op, args, rid = request["op"], request.get("args") or {}, request["id"]
        if op == "advance":
            n = int(args.get("steps", 1))
            state["step"] += n
            state["time"] += 0.125 * n
            print(f"advanced to step {state['step']}", file=sys.stderr)
            _send(resp, {"id": rid, "ok": True, "result": {
                "advanced": n, "time": state["time"], "finished": state["time"] >= 1.0,
                "history": [{"step": state["step"] - n + i + 1} for i in range(n)]}})
        elif op == "sleep":
            time.sleep(float(args["seconds"]))
            _send(resp, {"id": rid, "ok": True, "result": {"slept": args["seconds"]}})
        elif op == "crash":
            os._exit(9)
        elif op == "fail":
            _send(resp, {"id": rid, "ok": False, "error": "ValueError: nope",
                         "traceback": "tb"})
        elif op == "finalize":
            _send(resp, {"id": rid, "ok": True, "result": {"finalized": True}})
            return 0
        else:
            _send(resp, {"id": rid, "ok": True, "result": {"op": op, "args": args,
                                                           "cwd": os.getcwd()}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
