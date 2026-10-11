"""Tool registration: each module exposes register(mcp: FastMCP)."""

from __future__ import annotations


def register_all(mcp) -> None:
    from . import (environment, geometry, interop, mesh, mesh_edit, postprocess, project,
                   prompts, resources, scaffold, sessions, simulation, studies, visualize)

    for module in (environment, scaffold, mesh, mesh_edit, geometry, project, simulation,
                   postprocess, visualize, interop, resources, prompts, studies, sessions):
        module.register(mcp)
