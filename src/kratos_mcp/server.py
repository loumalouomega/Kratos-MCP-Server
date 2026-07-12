"""Kratos MCP server entry point.

Exposes Kratos Multiphysics to MCP clients over stdio. Kratos itself is
never imported here (see kratos_env module docstring); everything runs
through worker/runner subprocesses.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .tools import register_all

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,  # stdout belongs to the JSON-RPC transport
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

mcp = FastMCP(
    "kratos",
    instructions=(
        "Tools for driving Kratos Multiphysics finite element simulations: "
        "introspect the installation (kratos_check_installation, kratos_list_*), "
        "scaffold cases (create_project, list_templates, add_boundary_condition), "
        "generate and inspect MDPA meshes (mdpa_*), run simulations as background "
        "jobs (run_simulation, job_*), and post-process VTK results (results_*). "
        "Typical workflow: check installation -> create mesh -> create project -> "
        "add BCs/loads -> validate_case -> run_simulation -> results_summary."
    ),
)

register_all(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
