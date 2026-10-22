"""Documentation lookup tools backed by the public DeepWiki MCP server.

Opt-in (KRATOS_MCP_ENABLE_DEEPWIKI=1): these send the question text to
https://mcp.deepwiki.com. Answers describe the upstream repository, not the
Kratos build installed locally -- the kratos_list_* tools stay authoritative."""

from __future__ import annotations

from typing import Any

from .. import deepwiki

_DISABLED = ("DeepWiki lookups are disabled. Start the MCP server with "
             "KRATOS_MCP_ENABLE_DEEPWIKI=1 to enable them (questions are sent to "
             "https://mcp.deepwiki.com), or connect the DeepWiki MCP to your client directly.")


async def _lookup(tool: str, arguments: dict[str, Any], repo: str) -> dict[str, Any]:
    if not deepwiki.enabled():
        return {"error": _DISABLED}
    try:
        repo = deepwiki.validate_repo(repo)
        text = await deepwiki.call(tool, {"repoName": repo, **arguments})
    except deepwiki.DeepWikiError as exc:
        return {"error": str(exc)}
    text, truncated = deepwiki.truncate(text)
    return {"repo": repo, "content": text, "truncated": truncated, "source": "deepwiki"}


def register(mcp) -> None:

    @mcp.tool()
    async def kratos_docs_structure(repo: str = deepwiki.DEFAULT_REPO) -> dict[str, Any]:
        """List the documentation topics DeepWiki has for a GitHub repository
        (default KratosMultiphysics/Kratos). Use it to find what to read before
        kratos_docs_read. Disabled unless the server runs with
        KRATOS_MCP_ENABLE_DEEPWIKI=1; sends the repo name to mcp.deepwiki.com."""
        return await _lookup("read_wiki_structure", {}, repo)

    @mcp.tool()
    async def kratos_docs_read(repo: str = deepwiki.DEFAULT_REPO) -> dict[str, Any]:
        """Read the DeepWiki documentation of a GitHub repository (default
        KratosMultiphysics/Kratos). Output is truncated to about 20k characters
        (`truncated` says so); prefer kratos_docs_ask for a specific question.
        Disabled unless the server runs with KRATOS_MCP_ENABLE_DEEPWIKI=1."""
        return await _lookup("read_wiki_contents", {}, repo)

    @mcp.tool()
    async def kratos_docs_ask(question: str, repo: str = deepwiki.DEFAULT_REPO) -> dict[str, Any]:
        """Ask DeepWiki a free-form question about Kratos (or another indexed
        repo): solver settings, process parameters, application behaviour, the
        meaning of an error. Answers may be stale or differ from the Kratos
        build installed here -- cross-check with kratos_list_* /
        kratos_get_process_defaults. The question text is sent to
        mcp.deepwiki.com; disabled unless KRATOS_MCP_ENABLE_DEEPWIKI=1."""
        if not question.strip():
            return {"error": "question must not be empty"}
        return await _lookup("ask_question", {"question": question}, repo)
