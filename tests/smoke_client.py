"""Manual stdio smoke test: spawn the server as an MCP client would and
exercise a few tools end-to-end. Run with: uv run python tests/smoke_client.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "kratos_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"TOOLS ({len(names)}):", ", ".join(names))

            resources = await session.list_resources()
            templates = await session.list_resource_templates()
            print(f"RESOURCES: {len(resources.resources)} static, "
                  f"{len(templates.resourceTemplates)} templated")
            prompts = await session.list_prompts()
            print(f"PROMPTS: {sorted(p.name for p in prompts.prompts)}")

            result = await session.call_tool("kratos_check_installation", {})
            check = json.loads(result.content[0].text)
            print("CHECK:", {k: check.get(k) for k in ("importable", "version", "num_threads")})

            with tempfile.TemporaryDirectory() as tmp:
                mesh_path = str(Path(tmp) / "mesh.mdpa")
                result = await session.call_tool("mdpa_create_structured_mesh", {
                    "path": mesh_path, "kind": "rectangle",
                    "size": [1.0, 0.2], "divisions": [10, 2],
                })
                info = json.loads(result.content[0].text)
                print("MESH:", info["num_nodes"], "nodes,", info["num_elements"], "elements")

            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
