"""MDPA editing tools: nodes, elements, conditions, geometries, sub model
parts, properties, transforms and mesh analysis. Pure Python, no Kratos.

Every editing tool reads a .mdpa, applies one operation (see mesh_edit.py),
and writes the result: in place by default, or to ``output`` (never replacing
an existing file unless ``overwrite=true``). The result carries the operation
summary, the mesh summary and the validation issues of the edited mesh."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable

from .. import mdpa as mdpa_mod, mesh_edit as me


def _tool_errors(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            return {"error": f"{type(exc).__name__}: {exc}" if isinstance(exc, (KeyError, TypeError))
                    else str(exc)}

    return wrapper


def _load(path: str) -> tuple[Path, mdpa_mod.Mdpa]:
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"{src} does not exist")
    return src, mdpa_mod.read(src)


def _target(src: Path, mesh: mdpa_mod.Mdpa, output: str | None, overwrite: bool,
            required: bool = False) -> Path:
    """Destination of a write. In-place edits are refused when the file holds
    blocks this editor would drop; ``required`` means a separate file is mandatory."""
    dst = Path(output).expanduser().resolve() if output else src
    if dst.suffix != ".mdpa":
        dst = dst.with_suffix(".mdpa")
    if dst == src:
        if required:
            raise ValueError("output is required and must differ from the input file")
        if mesh.skipped_blocks:
            raise ValueError(
                f"{src.name} contains blocks the editor cannot represent "
                f"({', '.join(mesh.skipped_blocks[:8])}); editing in place would delete them. "
                "Pass output=<new file> instead.")
        return src
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} already exists; pass overwrite=true to replace it")
    return dst


def _finish(mesh: mdpa_mod.Mdpa, dst: Path, src: Path | None, result: Any = None) -> dict[str, Any]:
    issues = mesh.validate()
    mesh.write(dst)
    out: dict[str, Any] = {"written_to": str(dst), "in_place": dst == src}
    if result is not None:
        out["result"] = result
    out["mesh"] = mesh.inspect()
    out["valid"] = not issues
    if issues:
        out["issues"] = issues[:20]
    if mesh.skipped_blocks:
        out["warnings"] = [f"Blocks not supported by the editor were not written: "
                           f"{', '.join(mesh.skipped_blocks[:8])}"]
    return out


def _node_ids(mesh: mdpa_mod.Mdpa, node_ids: list[int] | None,
              selector: dict[str, Any] | None) -> list[int]:
    if (node_ids is None) == (selector is None):
        raise ValueError("give exactly one of node_ids or selector")
    if node_ids is not None:
        missing = [n for n in node_ids if n not in mesh.nodes]
        if missing:
            raise ValueError(f"Missing nodes {missing[:10]}")
        return [int(n) for n in node_ids]
    return me.select_nodes(mesh, selector)


def _singular(kind: str) -> str:
    if kind not in me.KINDS:
        raise ValueError(f"kind must be one of {list(me.KINDS)}")
    return kind


def register(mcp) -> None:

    @mcp.tool()
    @_tool_errors
    def mdpa_get_entities(
        path: str,
        kind: str = "element",
        ids: list[int] | None = None,
        entity_type: str | None = None,
        sub_model_part: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List elements, conditions or geometries of a .mdpa with their type,
        property id and node connectivity. kind: 'element' (default), 'condition'
        or 'geometry'. Filter by explicit ids, by entity_type (e.g.
        'LineLoadCondition2D2N') and/or by sub_model_part (dotted path). At most
        'limit' rows are returned, with the total count and a truncation flag."""
        _, mesh = _load(path)
        store = me._store(mesh, _singular(kind))
        wanted = set(ids) if ids is not None else None
        if sub_model_part:
            part = me._require_part(mesh, sub_model_part)
            members = set(me._part_list(part, kind))
            wanted = members if wanted is None else wanted & members
        rows = []
        for type_name in sorted(store):
            if entity_type and type_name != entity_type:
                continue
            for eid in sorted(store[type_name]):
                if wanted is not None and eid not in wanted:
                    continue
                entry = store[type_name][eid]
                row = {"id": eid, "type": type_name, "nodes": me._conn(entry)}
                if kind != "geometry":
                    row["property_id"] = entry[0]
                rows.append(row)
        return {"kind": kind, "count": len(rows), "truncated": len(rows) > limit,
                "entities": rows[:limit]}

    @mcp.tool()
    @_tool_errors
    def mdpa_select_nodes(path: str, selector: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        """Find nodes by geometry without changing anything. selector keys
        (combined as an intersection): ids, part (dotted SubModelPart), box
        {min:[x,y,z], max:[x,y,z]}, sphere {center, radius}, plane {point,
        normal} (nodes within 'tolerance' of the plane), expression (nodes where
        an expression in x, y, z is <= 0, e.g. 'x*x + y*y - 1.0' for a unit
        disc), tolerance (default 1e-9 x bounding-box diagonal), all=true.
        Returns the count, the first 'limit' ids and the bounding box of the
        selection. Pair with mdpa_create_sub_model_part to name the selection."""
        _, mesh = _load(path)
        ids = me.select_nodes(mesh, selector)
        coords = [mesh.nodes[n] for n in ids]
        box = None
        if coords:
            xs, ys, zs = zip(*coords)
            box = {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}
        return {"count": len(ids), "truncated": len(ids) > limit, "node_ids": ids[:limit],
                "bounding_box": box}

    @mcp.tool()
    @_tool_errors
    def mdpa_add_nodes(
        path: str,
        coordinates: list[list[float]],
        ids: list[int] | None = None,
        sub_model_part: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Add nodes ([x, y] or [x, y, z] each) to a .mdpa. Ids default to the
        next free ids; pass 'ids' to choose them. Optionally add them to an
        existing sub_model_part (dotted path). Edits in place unless 'output'
        is given."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        new = me.add_nodes(mesh, coordinates, ids, sub_model_part)
        return _finish(mesh, dst, src, {"node_ids": new})

    @mcp.tool()
    @_tool_errors
    def mdpa_move_nodes(
        path: str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        coordinates: list[list[float]] | None = None,
        offset: list[float] | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Move nodes: either set absolute 'coordinates' (one [x, y, z] per node,
        in the order of node_ids) or shift every chosen node by 'offset'. Choose
        the nodes with node_ids or a selector (see mdpa_select_nodes). Elements
        keep their connectivity, so check the result with mdpa_quality."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        chosen = _node_ids(mesh, node_ids, selector)
        count = me.move_nodes(mesh, chosen, coordinates, offset)
        return _finish(mesh, dst, src, {"moved": count})

    @mcp.tool()
    @_tool_errors
    def mdpa_delete_nodes(
        path: str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        cascade: bool = False,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Delete nodes (by id list or selector). Nodes still used by elements,
        conditions or geometries are refused unless cascade=true, which deletes
        those entities too. They are removed from every sub model part and from
        nodal data."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        chosen = _node_ids(mesh, node_ids, selector)
        return _finish(mesh, dst, src, me.delete_nodes(mesh, chosen, cascade))

    @mcp.tool()
    @_tool_errors
    def mdpa_add_entities(
        path: str,
        kind: str,
        entity_type: str,
        entities: list[dict[str, Any]],
        sub_model_part: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Add elements, conditions or geometries of one type. kind: 'element',
        'condition' or 'geometry'. entity_type is the Kratos name (e.g.
        'SmallDisplacementElement2D4N', 'LineLoadCondition2D2N', 'Line2D2'); its
        2D3N/3D8N-style suffix fixes the node count. Each item of 'entities' is
        {"nodes": [ids...], "id": optional, "property_id": optional (default 1,
        not for geometries)}. With sub_model_part the new entities and their nodes
        join that part (and its ancestors)."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        new = me.add_entities(mesh, _singular(kind), entity_type, entities, sub_model_part)
        return _finish(mesh, dst, src, {"ids": new})

    @mcp.tool()
    @_tool_errors
    def mdpa_delete_entities(
        path: str,
        kind: str,
        ids: list[int] | None = None,
        sub_model_part: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Delete elements, conditions or geometries by id (kind: 'element',
        'condition', 'geometry'), or every one that belongs to sub_model_part.
        They are removed from all sub model parts; nodes are kept."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        if (ids is None) == (sub_model_part is None):
            raise ValueError("give exactly one of ids or sub_model_part")
        chosen = ids if ids is not None else me._part_list(
            me._require_part(mesh, sub_model_part), _singular(kind))
        return _finish(mesh, dst, src, {"deleted": me.delete_entities(mesh, _singular(kind), chosen)})

    @mcp.tool()
    @_tool_errors
    def mdpa_set_entity_properties(
        path: str,
        kind: str,
        ids: list[int] | None = None,
        sub_model_part: str | None = None,
        property_id: int | None = None,
        new_type: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Change the property id (which Materials.json entry applies) and/or the
        Kratos type name of elements or conditions (kind 'element'/'condition';
        the new type must have the same node count). Choose entities by ids or
        by sub_model_part membership."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        if (ids is None) == (sub_model_part is None):
            raise ValueError("give exactly one of ids or sub_model_part")
        chosen = ids if ids is not None else me._part_list(
            me._require_part(mesh, sub_model_part), _singular(kind))
        changed = me.set_entity_properties(mesh, kind, chosen, property_id, new_type)
        return _finish(mesh, dst, src, {"changed": changed})

    @mcp.tool()
    @_tool_errors
    def mdpa_create_sub_model_part(
        path: str,
        name: str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        element_ids: list[int] | None = None,
        condition_ids: list[int] | None = None,
        geometry_ids: list[int] | None = None,
        include: list[str] | None = None,
        exist_ok: bool = False,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a sub model part (dotted name such as 'Structure_left' or
        'domain.fixed' for a child; the parent must exist) from node ids or a
        selector (see mdpa_select_nodes) plus optional explicit element /
        condition / geometry ids. include lists entity kinds ('element',
        'condition', 'geometry') whose members lying entirely on the part's nodes
        are added automatically -- use ['condition'] for a load surface. Nodes of
        added entities join too, and ancestors receive everything. exist_ok=true
        extends an existing part instead of failing."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        nodes: list[int] = []
        if node_ids is not None or selector is not None:
            nodes = _node_ids(mesh, node_ids, selector)
        part = me.create_part(mesh, name, nodes, element_ids or (), condition_ids or (),
                              geometry_ids or (), include or (), exist_ok)
        return _finish(mesh, dst, src, {"part": name, **part.tree()})

    @mcp.tool()
    @_tool_errors
    def mdpa_edit_sub_model_part(
        path: str,
        name: str,
        action: str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        element_ids: list[int] | None = None,
        condition_ids: list[int] | None = None,
        geometry_ids: list[int] | None = None,
        new_name: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Edit an existing sub model part (dotted path). action: 'add' members
        (nodes via node_ids/selector, elements, conditions, geometries; ancestors
        receive them too), 'remove' members (also from descendants), 'rename'
        (needs new_name, a single word) or 'delete' (the whole part, its
        children included; the entities themselves stay in the mesh)."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        nodes: list[int] = []
        if node_ids is not None or selector is not None:
            nodes = _node_ids(mesh, node_ids, selector)
        members = {"nodes": nodes, "elements": element_ids or (), "conditions": condition_ids or (),
                   "geometries": geometry_ids or ()}
        if action == "add":
            result: Any = me.add_members(mesh, name, **members)
        elif action == "remove":
            me.remove_members(mesh, name, **members)
            result = {"removed_from": name}
        elif action == "rename":
            if not new_name:
                raise ValueError("rename needs new_name")
            result = {"renamed_to": me.rename_part(mesh, name, new_name)}
        elif action == "delete":
            me.delete_part(mesh, name)
            result = {"deleted": name}
        else:
            raise ValueError("action must be add, remove, rename or delete")
        return _finish(mesh, dst, src, result)

    @mcp.tool()
    @_tool_errors
    def mdpa_transform(
        path: str,
        operation: str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        vector: list[float] | None = None,
        angle: float | None = None,
        axis: list[float] | None = None,
        origin: list[float] | None = None,
        factors: list[float] | float | None = None,
        point: list[float] | None = None,
        normal: list[float] | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Transform node coordinates (all nodes unless node_ids/selector is
        given). operation: 'translate' (vector), 'rotate' (angle in degrees about
        axis, default z, through origin), 'scale' (factors: one number or per
        axis, about origin) or 'mirror' (across the plane through point with
        normal). Mirror and negative scaling re-order the connectivity (including
        quadratic mid-edge and face nodes) so cells keep a positive measure."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        chosen = None
        if node_ids is not None or selector is not None:
            chosen = _node_ids(mesh, node_ids, selector)
        info = me.transform(mesh, operation, chosen, vector=vector, angle=angle, axis=axis,
                            origin=origin, factors=factors, point=point, normal=normal)
        return _finish(mesh, dst, src, info)

    @mcp.tool()
    @_tool_errors
    def mdpa_merge(
        path: str,
        other: str,
        output: str,
        merge_tolerance: float | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Append the mesh in 'other' to the one in 'path' and write 'output'
        (the inputs are untouched). Ids of 'other' are shifted past the first
        mesh's; sub model parts with the same name are united (their data, table
        and property references too). Tables, elemental/conditional data and all
        nodal data are carried over (colliding table ids are shifted). With
        merge_tolerance, coincident nodes (e.g. along a shared interface) are
        merged afterwards, so two blocks transformed into place become one
        conforming mesh. Inputs with blocks the editor cannot represent
        (Constraints, ...) are still refused: inspect skipped_blocks first."""
        src, base = _load(path)
        _, extra = _load(other)
        dst = _target(src, base, output, overwrite, required=True)
        info = me.merge_meshes(base, extra, merge_tolerance)
        return _finish(base, dst, src, info)

    @mcp.tool()
    @_tool_errors
    def mdpa_renumber(
        path: str,
        start: int = 1,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Renumber nodes, elements, conditions and geometries consecutively
        from 'start' (references in sub model parts and nodal data follow).
        Useful after deleting entities or merging meshes."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        return _finish(mesh, dst, src, me.renumber(mesh, start))

    @mcp.tool()
    @_tool_errors
    def mdpa_extract(
        path: str,
        sub_model_part: str,
        output: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write the contents of one sub model part (dotted path) as a new
        .mdpa: its entities, the nodes they use, its children, and the part
        itself as the root part. The input is untouched."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite, required=True)
        return _finish(me.extract_part(mesh, sub_model_part), dst, src,
                       {"extracted": sub_model_part})

    @mcp.tool()
    @_tool_errors
    def mdpa_extrude(
        path: str,
        output: str,
        heights: list[float],
        z0: float = 0.0,
        element_names: dict[str, str] | None = None,
        condition_names: dict[str, str] | None = None,
        cap_condition_names: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Extrude a planar (xy) mesh along z into a 3D mesh written to 'output'.
        heights are the layer thicknesses, e.g. [0.1] * 10 (z0 is the starting
        level). Triangles become prisms (3D6N), quads hexahedra (3D8N) and
        2-node conditions surface quads (3D4N). Type names map '2DnN' -> '3DmN'
        unless overridden: element_names / condition_names map old -> new name
        (e.g. {"LineLoadCondition2D2N": "SurfaceLoadCondition3D4N"}).
        cap_condition_names maps the cap face node count ("3" or "4") to a
        condition type to create conditions on the new 'zmin' / 'zmax' sub model
        parts (which always hold the cap nodes). Existing parts are carried over
        with all layers."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite, required=True)
        out = me.extrude(mesh, heights, z0, element_names, condition_names, cap_condition_names)
        return _finish(out, dst, src, {"layers": len(heights)})

    @mcp.tool()
    @_tool_errors
    def mdpa_convert_cells(
        path: str,
        mode: str,
        element_name: str | None = None,
        condition_name: str | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Convert cell kinds. mode 'quad_to_tri' splits every quadrilateral
        element into 2 triangles (e.g. SmallDisplacementElement2D4N ->
        ...2D3N); 'hex_to_tet' splits hexahedra into 6 tetrahedra (Kuhn split,
        conforming for structured meshes with consistent node ordering) and the
        boundary quads into triangles matching the tet faces; 'quadratic_to_linear'
        drops the mid-edge/face nodes of quadratic cells (tri6->tri, quad8/9->quad,
        tet10->tet, hex20/27->hex, wedge15->prism, line3->line; ids and sub model
        parts unchanged, mid nodes kept as unused nodes). Sub model parts
        follow. element_name / condition_name override the derived new type
        names. Other cell kinds are left alone. Needed e.g. for the
        convection-diffusion solver, which requires simplices."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        return _finish(mesh, dst, src, me.convert_cells(mesh, mode, element_name, condition_name))

    @mcp.tool()
    @_tool_errors
    def mdpa_extract_boundary(
        path: str,
        condition_name: str | dict[str, str],
        sub_model_part: str,
        element_part: str | None = None,
        selector: dict[str, Any] | None = None,
        property_id: int = 1,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create conditions on the free (unshared) faces of the elements --
        the way to get load/flux surfaces on a mesh that has none -- and put them
        in sub_model_part (created if needed). condition_name is a Kratos type
        (e.g. 'LineLoadCondition2D2N', 'SurfaceLoadCondition3D4N') or a map
        {"2": ..., "3": ..., "4": ...} by face node count (needed for prisms,
        mixed meshes and quadratic cells: quadratic edges have 3 nodes, tet10 /
        wedge tri faces 6, hex20 / wedge quad faces 8, hex27 faces 9).
        element_part restricts to one part's elements; selector
        (see mdpa_select_nodes, e.g. a plane) keeps only faces whose nodes are
        all selected, giving e.g. just the x=0 face. Faces are oriented outward
        for positively oriented elements (domain on the left of 2D edges). NOTE:
        with that orientation a positive POSITIVE_FACE_PRESSURE load acts along
        the outward normal (a tension); use a negative value for a compressive
        pressure. Supports linear and quadratic line, triangle, quad, tet, prism
        and hex cells (measures use the corners)."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        info = me.extract_boundary(mesh, condition_name, sub_model_part, element_part, selector,
                                   property_id)
        return _finish(mesh, dst, src, info)

    @mcp.tool()
    @_tool_errors
    def mdpa_set_properties(
        path: str,
        property_id: int | None = None,
        values: dict[str, Any] | None = None,
        remove: list[str] | None = None,
        model_part_data: dict[str, Any] | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Edit the mesh-embedded 'Begin Properties' block of one property id
        (values {"DENSITY": 7850.0} are added/replaced, 'remove' deletes names;
        lists are written space separated) and/or the 'ModelPartData' header.
        Most cases keep material data in Materials.json instead -- see
        materials_set_property."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        result: dict[str, Any] = {}
        if property_id is not None:
            result["properties"] = {property_id: me.set_properties(mesh, property_id, values, remove or ())}
        elif values or remove:
            raise ValueError("values/remove need property_id")
        if model_part_data:
            mesh.model_part_data.update({str(k): str(v) for k, v in model_part_data.items()})
            result["model_part_data"] = dict(mesh.model_part_data)
        if not result:
            raise ValueError("give property_id and/or model_part_data")
        return _finish(mesh, dst, src, result)

    @mcp.tool()
    @_tool_errors
    def mdpa_set_nodal_data(
        path: str,
        variable: str,
        value: float | str,
        node_ids: list[int] | None = None,
        selector: dict[str, Any] | None = None,
        fixed: bool = False,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write a 'Begin NodalData <variable>' block value (an initial
        condition stored in the mesh, e.g. TEMPERATURE) on the chosen nodes
        (node_ids or selector). A float writes a scalar row, a string such as
        "[3](1.0, 0.0, 0.0)" a vector row (kept verbatim); fixed=true marks the
        rows fixed (constrained)."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        chosen = _node_ids(mesh, node_ids, selector)
        return _finish(mesh, dst, src, {"set": me.set_nodal_data(mesh, variable, chosen, value,
                                                                fixed=fixed)})

    @mcp.tool()
    @_tool_errors
    def mdpa_get_data_blocks(path: str) -> dict[str, Any]:
        """List the data carried by a .mdpa besides the mesh itself: top-level
        Tables (id, variables, row count), 'Begin ElementalData' /
        'Begin ConditionalData' variables with row counts, vector and flag
        'Begin NodalData' blocks, and per-SubModelPart data/table/property
        references. All of these round-trip through every editing tool."""
        _, mesh = _load(path)
        parts: dict[str, Any] = {}
        for dotted in mdpa_mod.part_paths(mesh):
            part = mdpa_mod.find_part(mesh, dotted)
            if part is not None and (part.data or part.tables or part.properties):
                parts[dotted] = {"data": dict(part.data), "tables": list(part.tables),
                                 "properties": list(part.properties)}
        return {
            "tables": {tid: {"x_variable": t.x_variable, "y_variable": t.y_variable,
                             "rows": len(t.rows)} for tid, t in mesh.tables.items()},
            "elemental_data": {k: len(v) for k, v in mesh.elemental_data.items()},
            "conditional_data": {k: len(v) for k, v in mesh.conditional_data.items()},
            "vector_nodal_data": {k: len(v) for k, v in mesh.vector_nodal_data.items()},
            "nodal_flags": {k: len(v) for k, v in mesh.nodal_flags.items()},
            "sub_model_part_data": parts,
            "skipped_blocks": list(mesh.skipped_blocks),
        }

    @mcp.tool()
    @_tool_errors
    def mdpa_set_elemental_data(
        path: str,
        kind: str,
        variable: str,
        values: dict[str, Any] | None = None,
        remove_ids: list[int] | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Upsert 'Begin ElementalData <variable>' (kind 'elemental') or 'Begin
        ConditionalData <variable>' (kind 'conditional') rows: values maps
        element/condition ids to numbers, lists (written space separated) or raw
        strings such as "[3](0.0, 1.0, 0.0)" for vectors (kept verbatim).
        remove_ids deletes rows. Ids must exist; rows follow
        renumber/merge/delete automatically."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        if kind not in ("elemental", "conditional"):
            raise ValueError("kind must be 'elemental' or 'conditional'")
        parsed = {int(k): v for k, v in (values or {}).items()}
        result = me.set_elemental_data(mesh, kind, variable, parsed, remove_ids or ())
        return _finish(mesh, dst, src, result)

    @mcp.tool()
    @_tool_errors
    def mdpa_set_table(
        path: str,
        table_id: int,
        x_variable: str,
        y_variable: str,
        rows: list[list[float]],
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create or replace a top-level 'Begin Table <id> <x_variable>
        <y_variable>' block (piecewise-linear time series, e.g. TIME vs
        VELOCITY); rows are [x, y] pairs."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        return _finish(mesh, dst, src, me.set_table(mesh, table_id, x_variable, y_variable,
                                                   rows))

    @mcp.tool()
    @_tool_errors
    def mdpa_delete_table(
        path: str,
        table_id: int,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Delete a top-level 'Begin Table' block. Refused while a
        SubModelPart references the table."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        me.delete_table(mesh, table_id)
        return _finish(mesh, dst, src, {"deleted": table_id})

    @mcp.tool()
    @_tool_errors
    def mdpa_set_sub_model_part_data(
        path: str,
        name: str,
        data: dict[str, Any] | None = None,
        tables: list[int] | None = None,
        properties: list[int] | None = None,
        output: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Replace a SubModelPart's data references wholesale (each None leaves
        that reference unchanged): data is the 'Begin SubModelPartData'
        key-value pairs, tables the 'Begin SubModelPartTables' table ids (must
        exist as top-level Tables), properties the 'Begin SubModelPartProperties'
        property ids."""
        src, mesh = _load(path)
        dst = _target(src, mesh, output, overwrite)
        part = me._require_part(mesh, name)
        if data is not None:
            part.data = {str(k): str(v) for k, v in data.items()}
        if tables is not None:
            missing = [t for t in tables if t not in mesh.tables]
            if missing:
                raise ValueError(f"Missing tables {missing}")
            part.tables = [int(t) for t in tables]
        if properties is not None:
            part.properties = [int(p) for p in properties]
        return _finish(mesh, dst, src, {"part": name, "data": dict(part.data),
                                        "tables": list(part.tables),
                                        "properties": list(part.properties)})

    @mcp.tool()
    @_tool_errors
    def mdpa_quality(path: str, worst: int = 5) -> dict[str, Any]:
        """Mesh quality report, per element/condition type: count, measure
        (length/area/volume, from the corner nodes for quadratic cells)
        min/mean/max/total, aspect ratio (longest/shortest edge) with the ids
        of the worst cells, and the ids of inverted or degenerate cells
        (non-positive signed measure -- these crash or poison a
        Kratos run). Linear and quadratic line, triangle, quad, tet, prism and
        hex cells."""
        _, mesh = _load(path)
        report = me.quality(mesh, worst)
        bad = {label: {t: e["non_positive_or_invalid"] for t, e in by_type.items()
                       if "non_positive_or_invalid" in e}
               for label, by_type in report.items()}
        return {**report, "ok": not any(bad.values())}
