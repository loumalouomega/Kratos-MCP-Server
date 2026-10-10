"""Tool registration: each module exposes register(mcp: FastMCP)."""

from __future__ import annotations


def register_all(mcp) -> None:
    from . import (environment, interop, mesh, postprocess, prompts, resources,
                   scaffold, sessions, simulation, studies, visualize)

    for module in (environment, scaffold, mesh, simulation, postprocess, visualize,
                   interop, resources, prompts, studies, sessions):
        module.register(mcp)
