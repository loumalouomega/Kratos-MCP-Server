"""DeepWiki lookup tools: opt-in gating, validation, error mapping, and the
streamable-HTTP client against an in-process fake DeepWiki server."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from kratos_mcp import deepwiki
from kratos_mcp.tools import docs


def _tools():
    mcp = FastMCP("test")
    docs.register(mcp)
    return mcp


async def _run(name, **kwargs):
    result = await _tools().call_tool(name, kwargs)
    # call_tool returns (content, structured) for dict-returning tools
    return result[1] if isinstance(result, tuple) else result


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("KRATOS_MCP_ENABLE_DEEPWIKI", "KRATOS_MCP_DEEPWIKI_URL",
                "KRATOS_MCP_DEEPWIKI_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)


def test_enabled_parsing(monkeypatch):
    assert not deepwiki.enabled()
    for value, expected in [("1", True), ("true", True), ("yes", True), ("0", False), ("", False)]:
        monkeypatch.setenv("KRATOS_MCP_ENABLE_DEEPWIKI", value)
        assert deepwiki.enabled() is expected


def test_timeout_parsing(monkeypatch):
    assert deepwiki.timeout() == deepwiki.DEFAULT_TIMEOUT
    monkeypatch.setenv("KRATOS_MCP_DEEPWIKI_TIMEOUT", "5")
    assert deepwiki.timeout() == 5.0
    monkeypatch.setenv("KRATOS_MCP_DEEPWIKI_TIMEOUT", "abc")
    assert deepwiki.timeout() == deepwiki.DEFAULT_TIMEOUT


def test_validate_repo():
    assert deepwiki.validate_repo(" KratosMultiphysics/Kratos ") == "KratosMultiphysics/Kratos"
    for bad in ("", "Kratos", "a/b/c", "a b/c", "../etc"):
        with pytest.raises(deepwiki.DeepWikiError):
            deepwiki.validate_repo(bad)


def test_truncate():
    assert deepwiki.truncate("abc", 10) == ("abc", False)
    assert deepwiki.truncate("abcdef", 3) == ("abc", True)


async def test_disabled_by_default():
    out = await _run("kratos_docs_ask", question="what is a Process?")
    assert "KRATOS_MCP_ENABLE_DEEPWIKI" in out["error"]


async def test_enabled_passes_arguments(monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_ENABLE_DEEPWIKI", "1")
    seen = {}

    async def fake_call(tool, arguments):
        seen.update(tool=tool, arguments=arguments)
        return "x" * (deepwiki.MAX_CHARS + 5)

    monkeypatch.setattr(deepwiki, "call", fake_call)
    out = await _run("kratos_docs_ask", question="why?")
    assert seen == {"tool": "ask_question",
                    "arguments": {"repoName": "KratosMultiphysics/Kratos", "question": "why?"}}
    assert out["truncated"] is True and len(out["content"]) == deepwiki.MAX_CHARS
    assert out["source"] == "deepwiki"


async def test_validation_and_errors(monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_ENABLE_DEEPWIKI", "1")
    assert "owner/name" in (await _run("kratos_docs_read", repo="nope"))["error"]
    assert "empty" in (await _run("kratos_docs_ask", question="  "))["error"]

    async def boom(tool, arguments):
        raise deepwiki.DeepWikiError("unreachable")

    monkeypatch.setattr(deepwiki, "call", boom)
    assert (await _run("kratos_docs_structure"))["error"] == "unreachable"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def fake_deepwiki():
    port = _free_port()
    fake = FastMCP("fake-deepwiki", port=port, stateless_http=True, json_response=True,
                   transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @fake.tool()
    def ask_question(repoName: str, question: str) -> str:
        return f"{repoName}: {question}!"

    @fake.tool()
    def read_wiki_structure(repoName: str) -> str:
        raise ValueError("no wiki")

    server = uvicorn.Server(uvicorn.Config(fake.streamable_http_app(), host="127.0.0.1",
                                           port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


async def test_call_roundtrip(monkeypatch, fake_deepwiki):
    monkeypatch.setenv("KRATOS_MCP_DEEPWIKI_URL", fake_deepwiki)
    text = await deepwiki.call("ask_question", {"repoName": "a/b", "question": "hi"})
    assert text == "a/b: hi!"
    with pytest.raises(deepwiki.DeepWikiError, match="no wiki"):
        await deepwiki.call("read_wiki_structure", {"repoName": "a/b"})


async def test_call_unreachable(monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_DEEPWIKI_URL", f"http://127.0.0.1:{_free_port()}/mcp")
    monkeypatch.setenv("KRATOS_MCP_DEEPWIKI_TIMEOUT", "5")
    with pytest.raises(deepwiki.DeepWikiError, match="Could not reach|did not answer"):
        await deepwiki.call("ask_question", {"repoName": "a/b", "question": "hi"})


@pytest.mark.network
async def test_live_deepwiki(monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_ENABLE_DEEPWIKI", "1")
    out = await _run("kratos_docs_ask", question="What does the Newmark scheme do in Kratos?")
    assert "error" not in out, out
    assert out["content"]
