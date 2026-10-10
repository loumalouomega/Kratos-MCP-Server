"""Interactive session manager, exercised with a fake line-protocol worker.

No Kratos build is needed: tests/fake_session_worker.py speaks the same
protocol as kratos_mcp.session_worker. The real worker against a real build is
covered by test_session_integration.py."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from kratos_mcp import kratos_env, session_worker, sessions

TESTS_DIR = str(Path(__file__).parent)


@pytest.fixture(autouse=True)
def fake_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("PYTHONPATH", TESTS_DIR)
    monkeypatch.delenv("KRATOS_MCP_MAX_SESSIONS", raising=False)
    monkeypatch.delenv("KRATOS_MCP_ALLOW_EXEC", raising=False)
    env = kratos_env.KratosEnv(root=None, pythonpath=None, libs=None, source=None,
                               pip_installed=True, python=sys.executable)
    monkeypatch.setattr(sessions.kratos_env, "resolve", lambda: env)
    monkeypatch.setattr(sessions, "WORKER_MODULE", "fake_session_worker")
    yield
    sessions.close_all()


@pytest.fixture
def case(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    (d / "ProjectParameters.json").write_text("{}")
    return d


def test_start_handshake_and_banner_goes_to_log(case):
    info = sessions.start(str(case), timeout=30)
    assert info["state"] == "ready"
    assert info["analysis_class"] == "fake:Analysis"
    assert "FAKE BANNER" in sessions.logs(info["session_id"])


def test_request_roundtrip_and_cwd(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    out = sessions.request(sid, "echo", {"x": 1})
    assert out["args"] == {"x": 1}
    assert Path(out["cwd"]).resolve() == case.resolve()
    adv = sessions.request(sid, "advance", {"steps": 3})
    assert adv["advanced"] == 3 and adv["time"] == 0.375
    assert "advanced to step 3" in sessions.logs(sid)


def test_worker_error_is_reported_with_traceback(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    with pytest.raises(sessions.SessionError, match="ValueError: nope") as info:
        sessions.request(sid, "fail")
    assert info.value.traceback == "tb"
    assert sessions.status(sid)["state"] == "ready"  # an op error does not kill the session


def test_startup_failure_reports_error(case):
    (case / "fail_startup").write_text("")
    with pytest.raises(sessions.SessionError, match="boom"):
        sessions.start(str(case), timeout=30)
    (listed,) = sessions.list_sessions()
    assert listed["state"] == "dead"


def test_startup_timeout_kills_worker(case):
    (case / "hang_startup").write_text("")
    with pytest.raises(sessions.SessionError, match="did not become ready"):
        sessions.start(str(case), timeout=1.0)
    (listed,) = sessions.list_sessions()
    assert listed["state"] == "dead"


def test_busy_timeout_then_later_request_succeeds(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    with pytest.raises(sessions.SessionError) as info:
        sessions.request(sid, "sleep", {"seconds": 1.5}, timeout=0.3)
    assert info.value.busy is True
    assert sessions.status(sid)["busy"] is True
    # the next request queues behind the sleeping one and then completes
    assert sessions.request(sid, "echo", timeout=30)["op"] == "echo"
    assert sessions.status(sid)["busy"] is False


def test_crash_marks_session_dead_with_log_tail(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    with pytest.raises(sessions.SessionError, match="died"):
        sessions.request(sid, "crash")
    status = sessions.status(sid)
    assert status["state"] == "dead"
    assert status["returncode"] == 9
    assert "FAKE BANNER" in status["log_tail"]
    with pytest.raises(sessions.SessionError, match="dead"):
        sessions.request(sid, "echo")


def test_close_finalizes_and_kills_group(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    pid = sessions.status(sid)["pid"]
    out = sessions.close(sid)
    assert out["state"] == "closed" and out["finalized"] is True
    deadline = time.time() + 5
    while time.time() < deadline and sessions.process_group_alive(pid):
        time.sleep(0.1)
    assert not sessions.process_group_alive(pid)
    again = sessions.close(sid)  # idempotent
    assert again["state"] == "closed"
    with pytest.raises(sessions.SessionError, match="not live|closed"):
        sessions.request(sid, "echo")


def test_close_without_finalize_terminates_busy_worker(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    with pytest.raises(sessions.SessionError):
        sessions.request(sid, "sleep", {"seconds": 30}, timeout=0.2)
    out = sessions.close(sid, finalize=False)
    assert out["state"] == "closed" and out["finalized"] is False


def test_max_sessions_enforced(case, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_MAX_SESSIONS", "1")
    first = sessions.start(str(case), timeout=30)["session_id"]
    with pytest.raises(sessions.SessionError, match="Too many open sessions"):
        sessions.start(str(case), timeout=30)
    sessions.close(first)
    sessions.start(str(case), timeout=30)  # slot freed


def test_isolate_copies_case(case):
    (case / "marker.txt").write_text("x")
    info = sessions.start(str(case), isolate=True, timeout=30)
    work = Path(info["case_dir"])
    assert work != case and (work / "marker.txt").read_text() == "x"
    assert sessions.status(info["session_id"])["isolated"] is True


def test_missing_case_and_params(tmp_path):
    with pytest.raises(sessions.SessionError, match="not found"):
        sessions.start(str(tmp_path / "nope"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(sessions.SessionError, match="Parameters file not found"):
        sessions.start(str(tmp_path / "empty"))


def test_unknown_session_and_lost_after_restart(case, tmp_path):
    with pytest.raises(KeyError):
        sessions.status("nope")
    sid = sessions.start(str(case), timeout=30)["session_id"]
    sessions._live.clear()  # simulate a new server process
    assert sessions.status(sid)["state"] == "lost"
    with pytest.raises(sessions.SessionError, match="do not survive a restart"):
        sessions.request(sid, "echo")
    sessions.close(sid)  # must not raise


def test_concurrent_requests_are_matched_by_id(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    results = {}

    def call(i):
        results[i] = sessions.request(sid, "echo", {"i": i}, timeout=30)["args"]["i"]

    threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == {i: i for i in range(8)}


def test_history_records(case):
    sid = sessions.start(str(case), timeout=30)["session_id"]
    sessions.record_history(sid, [{"step": 1}, {"step": 2}])
    assert [r["step"] for r in sessions.history(sid)] == [1, 2]
    assert sessions.status(sid)["steps_recorded"] == 2


# -- pure helpers of the real worker (importable without Kratos) -----------

def test_summarize_scalar_and_vector():
    s = session_worker.summarize([(1, None, 2.0), (2, None, -5.0), (3, None, 1.0)])
    assert s["max"] == 5.0 and s["argmax"]["id"] == 2 and s["argmin"]["id"] == 3
    v = session_worker.summarize([(1, [0, 0, 0], [3.0, 4.0, 0.0]), (2, [1, 0, 0], [0.0, 0.0, 1.0])])
    assert v["max"] == 5.0 and v["argmax"]["coordinates"] == [0, 0, 0]
    assert v["component_max"] == [3.0, 4.0, 1.0]
    assert session_worker.summarize([]) == {"count": 0}


def test_json_pointer_get():
    doc = {"a": {"b": [10, {"c/d": 3}]}}
    assert session_worker.json_pointer_get(doc, "") == doc
    assert session_worker.json_pointer_get(doc, "/a/b/0") == 10
    assert session_worker.json_pointer_get(doc, "/a/b/1/c~1d") == 3
    with pytest.raises(KeyError):
        session_worker.json_pointer_get(doc, "/a/zzz")
    with pytest.raises(ValueError):
        session_worker.json_pointer_get(doc, "a/b")


def test_to_plain_handles_non_finite_and_nesting():
    assert session_worker.to_plain({"x": (1, 2.5, float("inf"))}) == {"x": [1, 2.5, "inf"]}


def test_exec_gating_flags():
    assert session_worker.exec_allowed({}) is False
    assert session_worker.exec_allowed({"KRATOS_MCP_ALLOW_EXEC": "1"}) is True


# -- MCP tool layer -----------------------------------------------------------

async def test_tools_registered_and_exec_disabled_by_default(case):
    from mcp.server.fastmcp import FastMCP
    from kratos_mcp.tools import sessions as tools

    mcp = FastMCP("t")
    tools.register(mcp)
    names = {t.name for t in await mcp.list_tools()}
    assert {"session_start", "session_list", "session_status", "session_get_parameters",
            "session_advance", "session_model_info", "session_field_summary",
            "session_inspect_node", "session_inspect_element",
            "session_set_nodal_values", "session_exec_python", "session_close"} <= names

    _, started = await mcp.call_tool("session_start", {"case_dir": str(case), "timeout": 30})
    sid = started["session_id"]
    _, out = await mcp.call_tool("session_exec_python", {"session_id": sid, "code": "result=1"})
    assert "KRATOS_MCP_ALLOW_EXEC" in out["error"]
    _, adv = await mcp.call_tool("session_advance", {"session_id": sid, "steps": 2})
    assert adv["advanced"] == 2
    assert len(sessions.history(sid)) == 2
    _, missing = await mcp.call_tool("session_status", {"session_id": "nope"})
    assert "No such session" in missing["error"]
    _, closed = await mcp.call_tool("session_close", {"session_id": sid})
    assert closed["state"] == "closed"
