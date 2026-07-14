"""Manual stdio smoke test: spawn the server as an MCP client would and
exercise a few tools end-to-end. Run with: uv run python tests/smoke_client.py"""

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

                # Knowledge-layer tools (Flowgraph-inspired).
                result = await session.call_tool("list_material_presets", {})
                presets = json.loads(result.content[0].text)
                print("MATERIAL PRESETS:", sorted(presets))

                result = await session.call_tool("list_linear_solver_presets", {})
                print("LINEAR SOLVER PRESETS:", sorted(json.loads(result.content[0].text)))

                result = await session.call_tool("kratos_get_process_defaults", {
                    "python_module": "assign_scalar_variable_process"})
                pd = json.loads(result.content[0].text)
                print("PROCESS DEFAULTS:", "error" if "error" in pd
                      else sorted(pd.get("default_settings", {})))

                # Multi-stage scaffold + explain + Flowgraph round-trip.
                ms_dir = str(Path(tmp) / "ms")
                result = await session.call_tool("create_multistage_project", {
                    "directory": ms_dir,
                    "stages": [
                        {"name": "s1", "template": "structural_static"},
                        {"name": "s2", "template": "structural_static"},
                    ],
                })
                ms = json.loads(result.content[0].text)
                print("MULTISTAGE:", ms.get("execution_list", ms.get("error")))

                pp_file = str(Path(ms_dir) / "ProjectParameters.json")
                result = await session.call_tool("explain_project_parameters",
                                                 {"parameters_file": pp_file})
                summary = json.loads(result.content[0].text)
                print("EXPLAIN:", summary.get("kind"), summary.get("execution_list"))

                graph_file = str(Path(tmp) / "graph.json")
                await session.call_tool("export_case_to_flowgraph",
                                        {"parameters_file": pp_file, "output_file": graph_file})
                result = await session.call_tool("import_flowgraph_to_case",
                                                 {"graph_file": graph_file})
                back = json.loads(result.content[0].text)
                print("FLOWGRAPH ROUND-TRIP:", "ok" if "parameters" in back else back)

            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
