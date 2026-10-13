"""MDPA mesh tools: inspect, validate and generate meshes."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import anyio

from .. import bridge, mdpa as mdpa_mod, remesh as remesh_mod


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
    async def mesh_remesh(
        input_mdpa: str,
        output_mdpa: str,
        strategy: str = "levelset",
        region: dict[str, Any] | None = None,
        distance_expression: str | None = None,
        field_variable: str | None = None,
        field_expression: str | None = None,
        min_size: float | None = None,
        max_size: float | None = None,
        transition_distance: float | None = None,
        preserve_sub_model_parts: list[str] | None = None,
        freeze_sub_model_parts: list[str] | None = None,
        check_fields: list[dict[str, str]] | None = None,
        interpolation_error: float | None = None,
        field_results: str | None = None,
        field_series: str | None = None,
        field_time: float | None = None,
        field_step: int | None = None,
        passes: int = 1,
        overwrite: bool = False,
        applications: list[str] | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Adaptively remesh a .mdpa with MMG (MeshingApplication) and write a new
        .mdpa; returns before/after mesh summaries, a boundary preservation check
        and the field transfer error. Needs KratosMeshingApplication built with MMG
        (see kratos_install). Strategies: 'levelset' refines around a signed
        distance given as 'region' ({"type": "sphere", "center": [x, y], "radius": r}
        or {"type": "box", "min": [...], "max": [...]}; "mode": "inside" (default,
        the whole region gets min_size) or "surface") or as 'distance_expression'
        in x, y, z; 'hessian' adapts to the second derivatives of the analytic nodal
        field field_expression stored in the Kratos scalar variable field_variable;
        'uniform' remeshes to the single element size min_size. Sizes default to
        max_size = mean input element size, min_size = max_size/4; the size grows
        from min_size to max_size over transition_distance (default 3*max_size).
        preserve_sub_model_parts (default: every part with conditions) are checked
        to keep their conditions and node bounding box; freeze_sub_model_parts
        additionally forbids MMG from touching their nodes. check_fields
        ([{"variable": "TEMPERATURE", "expression": "x + 2*y"}]) assigns analytic
        nodal values before remeshing and reports how well the interpolation
        reproduces them on the new nodes. Elements must be linear simplices
        (triangles or tetrahedra, not mixed; quads, hexes and quadratic cells are
        refused up front - convert with mdpa_convert_cells first). The result
        reports mesh_kind: planar2d (the verified path), volume3d (tetrahedra) or
        surface3d (triangles spanning z); the 3D kinds are wired to MmgProcess3D /
        MmgProcess3DSurfaces but not verified against reference numbers, which a
        warnings entry says. For 'hessian', interpolation_error (default 0.04) is the
        target P1 interpolation error - smaller means finer. A metric computed on a
        coarse mesh under-resolves a sharp feature: passes=3 repeats remeshing on
        the previous result (the analytic field is re-evaluated on every new mesh),
        which on the worked example halves the interpolation error at the same
        element count. Instead of field_expression, 'hessian' can adapt to a SOLVED
        field: field_results is a result-index.json (a job's results index; pick the
        record with field_time or field_step, default the last one) or a single ASCII
        .vtk/.vtu file whose point data holds field_variable on exactly this mesh's
        nodes (matched by coordinates). The transferred field is written to the output
        .mdpa as a 'Begin NodalData' block and carried between passes. Existing output
        is only replaced with overwrite=true."""
        src = Path(input_mdpa).expanduser().resolve()
        dst = Path(output_mdpa).expanduser().resolve()
        if dst.suffix != ".mdpa":
            dst = dst.with_suffix(".mdpa")
        if not src.is_file():
            return {"error": f"{src} does not exist"}
        if dst == src:
            return {"error": "output_mdpa must differ from input_mdpa"}
        if dst.exists() and not overwrite:
            return {"error": f"{dst} already exists; pass overwrite=true to replace it"}
        try:
            before = mdpa_mod.read(src)
            field_values = None
            if field_results is not None:
                if field_expression is not None:
                    return {"error": "give either field_expression or field_results, not both"}
                if not field_variable:
                    return {"error": "field_results requires field_variable"}
                picked = remesh_mod.field_from_result(
                    before, field_results, field_variable, series=field_series,
                    time=field_time, step=field_step)
                field_values = picked["values"]
            names = (list(preserve_sub_model_parts) if preserve_sub_model_parts is not None
                     else remesh_mod.boundary_parts(before))
            request = remesh_mod.build_request(
                before, strategy=strategy, region=region,
                distance_expression=distance_expression, field_variable=field_variable,
                field_expression=field_expression, min_size=min_size, max_size=max_size,
                transition_distance=transition_distance,
                freeze_sub_model_parts=freeze_sub_model_parts, check_fields=check_fields,
                interpolation_error=interpolation_error, field_values=field_values)
        except (ValueError, IndexError) as exc:
            return {"error": str(exc)}
        issues = before.validate()
        if issues:
            return {"error": "The input mesh is invalid", "issues": issues[:20]}
        if not isinstance(passes, int) or isinstance(passes, bool) or not 1 <= passes <= 6:
            return {"error": "passes must be an integer between 1 and 6"}

        def work() -> tuple[dict[str, Any], list[dict[str, int]]]:
            support = bridge.run_op("meshing_support")
            variant = remesh_mod.MMG_VARIANT[request["mesh_kind"]]
            # builds cached before the per-variant keys existed only know "mmg"
            if not support.get(variant, support.get("mmg")):
                raise remesh_mod.MeshingUnavailable(support)
            dst.parent.mkdir(parents=True, exist_ok=True)
            history: list[dict[str, int]] = []
            current, result = src, {}
            with tempfile.TemporaryDirectory(prefix="kratos-mcp-remesh-") as scratch:
                for index in range(passes):
                    target = dst if index == passes - 1 else Path(scratch) / f"pass{index}.mdpa"
                    pass_request = request if index == 0 else remesh_mod.carry_fields(request)
                    op_args: dict[str, Any] = {"input": str(current), "output": str(target),
                                               "request": pass_request}
                    if applications:
                        op_args["applications"] = applications
                    result = bridge.run_op("remesh_mdpa", op_args, timeout=timeout,
                                           use_cache=False)
                    remeshed = mdpa_mod.read(target)
                    fields = result.get("fields") or {}
                    if fields:
                        for name, by_id in fields.items():
                            remeshed.nodal_data[name] = {int(k): float(v)
                                                         for k, v in by_id.items()}
                        remeshed.write(target)
                    info = remeshed.inspect()
                    history.append({"pass": index + 1, "nodes": info["num_nodes"],
                                    "elements": info["num_elements"]})
                    current = target
            return result, history

        try:
            result, history = await anyio.to_thread.run_sync(work)
        except remesh_mod.MeshingUnavailable as exc:
            return {"error": "MMG remeshing is not available in the configured Kratos build "
                             "(KratosMeshingApplication missing or built without the MMG "
                             "variant this mesh needs). "
                             "Install it with kratos_install(applications=['MeshingApplication']) "
                             "or point KRATOS_ROOT at a build that has it.",
                    "meshing_support": exc.support}
        except bridge.BridgeError as exc:
            return {"error": exc.details()}

        after = mdpa_mod.read(dst)
        warning = remesh_mod.UNVERIFIED_KINDS.get(request["mesh_kind"])
        return {"written_to": str(dst), "strategy": strategy, "passes": history,
                "mesh_kind": request["mesh_kind"],
                **({"warnings": [warning]} if warning else {}),
                "settings": request["settings"],
                **remesh_mod.report(before, after, names),
                "transfer": result["transfer"],
                "fields_written": sorted(after.nodal_data)}

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
            part = mdpa_mod.find_part(m, sub_model_part)
            if part is None:
                return {"error": f"SubModelPart '{sub_model_part}' not found",
                        "available": mdpa_mod.part_paths(m)}
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
