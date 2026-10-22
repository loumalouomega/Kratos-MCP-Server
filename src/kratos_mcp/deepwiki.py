"""Opt-in client for the public DeepWiki MCP server (https://mcp.deepwiki.com/mcp).

DeepWiki indexes ``KratosMultiphysics/Kratos`` and answers free-form questions
about it. This is the only module of the server that talks to the network
(besides pip in ``kratos_env``), so it is disabled unless the server was started
with ``KRATOS_MCP_ENABLE_DEEPWIKI=1``. No Kratos import; nothing happens at
import time."""

from __future__ import annotations

import os
import re
from typing import Any

import anyio

DEFAULT_URL = "https://mcp.deepwiki.com/mcp"
DEFAULT_REPO = "KratosMultiphysics/Kratos"
DEFAULT_TIMEOUT = 60.0
MAX_CHARS = 20000

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DeepWikiError(RuntimeError):
    """A DeepWiki operation failed (message is safe to return to the client)."""


def enabled() -> bool:
    return os.environ.get("KRATOS_MCP_ENABLE_DEEPWIKI", "") in ("1", "true", "yes")


def url() -> str:
    return os.environ.get("KRATOS_MCP_DEEPWIKI_URL", "") or DEFAULT_URL


def timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("KRATOS_MCP_DEEPWIKI_TIMEOUT", DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


def validate_repo(repo: str) -> str:
    repo = (repo or "").strip()
    if not _REPO_RE.match(repo) or any(part.strip('.') == '' for part in repo.split('/')):
        raise DeepWikiError(f"repo must look like 'owner/name', got {repo!r}")
    return repo


def truncate(text: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _describe(exc: BaseException) -> str:
    inner = getattr(exc, "exceptions", None)
    if inner:
        return "; ".join(_describe(e) for e in inner)
    return f"{type(exc).__name__}: {exc}"


async def call(tool: str, arguments: dict[str, Any]) -> str:
    """Call one DeepWiki tool and return its concatenated text content.

    Raises DeepWikiError on timeout, transport failure or a tool-level error."""
    # Imported lazily: keeps server start-up free of the HTTP client stack.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    try:
        with anyio.fail_after(timeout()):
            async with streamable_http_client(url()) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
    except TimeoutError:
        raise DeepWikiError(f"DeepWiki did not answer within {timeout():g} s "
                            "(raise KRATOS_MCP_DEEPWIKI_TIMEOUT to wait longer)") from None
    except Exception as exc:  # transport failures surface as (grouped) exceptions
        raise DeepWikiError(f"Could not reach DeepWiki at {url()}: {_describe(exc)}") from exc

    text = "\n".join(getattr(block, "text", "") for block in result.content
                     if getattr(block, "type", "") == "text").strip()
    if result.isError:
        raise DeepWikiError(f"DeepWiki returned an error: {text or 'no details'}")
    return text
