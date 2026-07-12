"""Tool registration: each module exposes register(mcp: FastMCP)."""

from __future__ import annotations


def register_all(mcp) -> None:
    from . import (environment, mesh, postprocess, prompts, resources, scaffold,
                   simulation, visualize)

    for module in (environment, scaffold, mesh, simulation, postprocess, visualize,
                   resources, prompts):
        module.register(mcp)
