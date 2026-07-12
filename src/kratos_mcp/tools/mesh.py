"""MDPA mesh tools: inspect, validate and generate meshes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio

from .. import bridge, mdpa as mdpa_mod


def register(mcp) -> None:

    @mcp.tool()
    def mdpa_inspect(path: str) -> dict[str, Any]:
        """Inspect a Kratos .mdpa mesh file: node/element/condition counts by
        type, bounding box, property ids and the SubModelPart tree with
        entity counts."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        return mdpa_mod.read(p).inspect()

    @mcp.tool()
    async def mdpa_validate(path: str, deep: bool = False) -> dict[str, Any]:
        """Validate a .mdpa file: dangling node/element/condition references
        and empty submodelparts (pure-Python lint). With deep=true the file
        is additionally round-tripped through the real Kratos ModelPartIO."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        try:
            issues = mdpa_mod.read(p).validate()
        except (ValueError, IndexError) as exc:
            return {"valid": False, "issues": [f"Parse error: {exc}"]}
        result: dict[str, Any] = {"valid": not issues, "issues": issues}
        if deep:
            try:
                result["kratos_read"] = await anyio.to_thread.run_sync(
                    lambda: bridge.run_op("read_mdpa_deep", {"path": str(p)}))
            except bridge.BridgeError as exc:
                result["valid"] = False
                result["kratos_read"] = {"read_ok": False, "error": exc.details()}
        return result

    @mcp.tool()
    def mdpa_create_structured_mesh(
        path: str,
        kind: str,
        size: list[float],
        divisions: list[int],
        element_name: str | None = None,
        condition_name: str | None = None,
        triangles: bool = False,
    ) -> dict[str, Any]:
        """Generate a structured mesh and write it as .mdpa. kind: 'line'
        (size=[L], divisions=[n], submodelparts start/end), 'rectangle'
        (size=[W,H], divisions=[nx,ny], edge parts left/right/bottom/top,
        quads or triangles), or 'box' (size=[Lx,Ly,Lz], divisions=[nx,ny,nz],
        hexahedra, face parts xmin/xmax/ymin/ymax/zmin/zmax). All variants
        include a 'domain' submodelpart with every node and element; boundary
        parts carry conditions of condition_name for applying surface loads.
        Defaults: rectangle SmallDisplacementElement2D4N + LineLoadCondition2D2N,
        box SmallDisplacementElement3D8N + SurfaceLoadCondition3D4N."""
        try:
            if kind == "line":
                mesh = mdpa_mod.create_line_mesh(
                    length=size[0], num_elements=divisions[0],
                    element_name=element_name or "TrussLinearElement2D2N",
                    condition_name=condition_name)
            elif kind == "rectangle":
                mesh = mdpa_mod.create_rectangle_mesh(
                    width=size[0], height=size[1], nx=divisions[0], ny=divisions[1],
                    element_name=element_name or (
                        "SmallDisplacementElement2D3N" if triangles
                        else "SmallDisplacementElement2D4N"),
                    condition_name=condition_name or "LineLoadCondition2D2N",
                    triangles=triangles)
            elif kind == "box":
                mesh = mdpa_mod.create_box_mesh(
                    lx=size[0], ly=size[1], lz=size[2],
                    nx=divisions[0], ny=divisions[1], nz=divisions[2],
                    element_name=element_name or "SmallDisplacementElement3D8N",
                    condition_name=condition_name or "SurfaceLoadCondition3D4N")
            else:
                return {"error": f"Unknown kind '{kind}'. Use line, rectangle or box."}
        except IndexError:
            return {"error": "size/divisions have too few entries for this kind"}
        written = mesh.write(Path(path).expanduser().resolve())
        return {"written_to": str(written), **mesh.inspect()}

    @mcp.tool()
    def mdpa_get_nodes(
        path: str,
        sub_model_part: str | None = None,
        node_ids: list[int] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return node ids and coordinates from a .mdpa file, optionally
        restricted to one submodelpart (dotted path like 'domain' or
        'outer.inner') or an explicit id list. At most 'limit' nodes are
        returned (with a truncation flag)."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        m = mdpa_mod.read(p)

        selected: list[int]
        if sub_model_part:
            part = None
            parts = m.sub_model_parts
            for name in sub_model_part.split("."):
                part = parts.get(name)
                if part is None:
                    return {"error": f"SubModelPart '{sub_model_part}' not found",
                            "available": sorted(parts)}
                parts = part.children
            selected = part.nodes
        elif node_ids:
            selected = node_ids
        else:
            selected = sorted(m.nodes)

        out = []
        for nid in selected[:limit]:
            if nid in m.nodes:
                x, y, z = m.nodes[nid]
                out.append({"id": nid, "x": x, "y": y, "z": z})
        return {"count": len(selected), "truncated": len(selected) > limit, "nodes": out}
