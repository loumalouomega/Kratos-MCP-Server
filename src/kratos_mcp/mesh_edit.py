"""Kratos-free editing operations on an :class:`~kratos_mcp.mdpa.Mdpa` mesh.

Every function mutates the mesh it is given and raises ``ValueError`` with a
readable message on bad input (the tool layer turns that into
``{"error": ...}``). Nothing here imports Kratos, so all of it works on a bare
install and is unit-tested directly.

Entity kinds are ``"element"``, ``"condition"`` and ``"geometry"``. Elements and
conditions are stored as ``{type: {id: (property_id, [node ids])}}``, geometries
as ``{type: {id: [node ids]}}`` (they have no property).
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any, Callable, Iterable

import numpy as np

from . import mdpa as mdpa_mod
from .mdpa import Mdpa, SubModelPart
from .remesh import compile_expression

KINDS = ("element", "condition", "geometry")


# ----------------------------------------------------------------- utilities

def _store(mesh: Mdpa, kind: str) -> dict[str, dict[int, Any]]:
    if kind == "element":
        return mesh.elements
    if kind == "condition":
        return mesh.conditions
    if kind == "geometry":
        return mesh.geometries
    raise ValueError(f"Unknown entity kind '{kind}'. Use element, condition or geometry.")


def _part_list(part: SubModelPart, kind: str) -> list[int]:
    return {"element": part.elements, "condition": part.conditions,
            "geometry": part.geometries}[kind]


def _conn(entry: Any) -> list[int]:
    return entry[1] if isinstance(entry, tuple) else entry


def _all_ids(mesh: Mdpa, kind: str) -> set[int]:
    return {i for by_id in _store(mesh, kind).values() for i in by_id}


def _walk(mesh: Mdpa) -> Iterable[SubModelPart]:
    def rec(part: SubModelPart) -> Iterable[SubModelPart]:
        yield part
        for child in part.children.values():
            yield from rec(child)

    for part in mesh.sub_model_parts.values():
        yield from rec(part)


def _require_part(mesh: Mdpa, path: str) -> SubModelPart:
    part = mdpa_mod.find_part(mesh, path)
    if part is None:
        raise ValueError(f"SubModelPart '{path}' not found. "
                         f"Available: {mdpa_mod.part_paths(mesh)}")
    return part


def _dedupe(ids: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def _vec3(value: Any, name: str, default: float = 0.0) -> np.ndarray:
    try:
        arr = [float(v) for v in value]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a list of numbers") from None
    if not 1 <= len(arr) <= 3:
        raise ValueError(f"{name} needs 1-3 numbers, got {len(arr)}")
    return np.array(arr + [default] * (3 - len(arr)))


def _next_id(existing: Iterable[int]) -> int:
    return max(existing, default=0) + 1


def _diag(mesh: Mdpa) -> float:
    box = mesh.bounding_box()
    if box is None:
        return 1.0
    return float(np.linalg.norm(np.array(box["max"]) - np.array(box["min"]))) or 1.0


# ------------------------------------------------------------------ selection

def select_nodes(mesh: Mdpa, selector: dict[str, Any] | None) -> list[int]:
    """Node ids matching ``selector`` (keys combine as an intersection).

    Keys: ``ids`` (list), ``part`` (dotted SubModelPart), ``box``
    ({min, max}), ``sphere`` ({center, radius}), ``plane`` ({point, normal};
    nodes within ``tolerance`` of it), ``expression`` (nodes where the
    expression in x, y, z is <= 0, a signed distance), ``tolerance`` (default
    1e-9 x the bounding-box diagonal), ``all`` (every node). An empty or
    missing selector is an error: select something explicitly."""
    if not selector:
        raise ValueError("selector is empty; give ids, part, box, sphere, plane, "
                         "expression or all=true")
    known = {"ids", "part", "box", "sphere", "plane", "expression", "tolerance", "all"}
    unknown = set(selector) - known
    if unknown:
        raise ValueError(f"Unknown selector keys {sorted(unknown)}. Use {sorted(known)}")
    tol = float(selector.get("tolerance", 1e-9 * _diag(mesh)))
    ids = sorted(mesh.nodes)
    if "ids" in selector:
        wanted = {int(i) for i in selector["ids"]}
        missing = sorted(wanted - set(mesh.nodes))
        if missing:
            raise ValueError(f"Selector ids reference missing nodes {missing[:10]}")
        ids = [i for i in ids if i in wanted]
    if "part" in selector:
        in_part = set(_require_part(mesh, selector["part"]).nodes)
        ids = [i for i in ids if i in in_part]
    if not ids:
        return []
    pts = np.array([mesh.nodes[i] for i in ids])
    keep = np.ones(len(ids), dtype=bool)
    if "box" in selector:
        lo = _vec3(selector["box"].get("min", [-math.inf] * 3), "box.min", -math.inf)
        hi = _vec3(selector["box"].get("max", [math.inf] * 3), "box.max", math.inf)
        keep &= np.all((pts >= lo - tol) & (pts <= hi + tol), axis=1)
    if "sphere" in selector:
        center = _vec3(selector["sphere"]["center"], "sphere.center")
        radius = float(selector["sphere"]["radius"])
        keep &= np.linalg.norm(pts - center, axis=1) <= radius + tol
    if "plane" in selector:
        point = _vec3(selector["plane"]["point"], "plane.point")
        normal = _vec3(selector["plane"]["normal"], "plane.normal")
        length = float(np.linalg.norm(normal))
        if length == 0:
            raise ValueError("plane.normal must be non-zero")
        keep &= np.abs((pts - point) @ (normal / length)) <= tol
    if "expression" in selector:
        fn = compile_expression(selector["expression"])
        keep &= np.array([fn(*p) <= tol for p in pts])
    return [i for i, k in zip(ids, keep) if k]


def entities_on_nodes(mesh: Mdpa, kind: str, node_ids: Iterable[int],
                      require: str = "all") -> list[int]:
    """Entity ids whose nodes are all (``require='all'``) or any (``'any'``) in node_ids."""
    nodes = set(node_ids)
    test = all if require == "all" else any
    return sorted(eid for by_id in _store(mesh, kind).values()
                  for eid, entry in by_id.items() if test(n in nodes for n in _conn(entry)))


# ---------------------------------------------------------------------- nodes

def add_nodes(mesh: Mdpa, coordinates: list[list[float]],
              ids: list[int] | None = None, part: str | None = None) -> list[int]:
    if ids is not None and len(ids) != len(coordinates):
        raise ValueError("ids and coordinates must have the same length")
    new_ids: list[int] = []
    next_id = _next_id(mesh.nodes)
    for index, xyz in enumerate(coordinates):
        nid = int(ids[index]) if ids is not None else next_id
        if nid in mesh.nodes or nid in new_ids:
            raise ValueError(f"Node id {nid} already exists")
        mesh.nodes[nid] = tuple(float(v) for v in _vec3(xyz, f"coordinates[{index}]"))
        new_ids.append(nid)
        next_id = max(next_id, nid + 1)
    if part:
        add_members(mesh, part, nodes=new_ids)
    return new_ids


def move_nodes(mesh: Mdpa, node_ids: list[int], coordinates: list[list[float]] | None = None,
               offset: list[float] | None = None) -> int:
    if (coordinates is None) == (offset is None):
        raise ValueError("give exactly one of coordinates (absolute, one per node) or "
                         "offset (added to every node)")
    missing = [n for n in node_ids if n not in mesh.nodes]
    if missing:
        raise ValueError(f"Missing nodes {missing[:10]}")
    if coordinates is not None:
        if len(coordinates) != len(node_ids):
            raise ValueError("coordinates needs one entry per node id")
        for nid, xyz in zip(node_ids, coordinates):
            mesh.nodes[nid] = tuple(float(v) for v in _vec3(xyz, "coordinates"))
    else:
        d = _vec3(offset, "offset")
        for nid in node_ids:
            mesh.nodes[nid] = tuple(float(v) for v in np.array(mesh.nodes[nid]) + d)
    return len(node_ids)


def delete_nodes(mesh: Mdpa, node_ids: Iterable[int], cascade: bool = False) -> dict[str, int]:
    doomed = {int(n) for n in node_ids}
    missing = sorted(doomed - set(mesh.nodes))
    if missing:
        raise ValueError(f"Missing nodes {missing[:10]}")
    users = {kind: entities_on_nodes(mesh, kind, doomed, "any") for kind in KINDS}
    if not cascade and any(users.values()):
        raise ValueError(
            "Nodes are used by "
            + ", ".join(f"{len(v)} {k}(s)" for k, v in users.items() if v)
            + "; pass cascade=true to delete those entities too")
    removed = {kind: delete_entities(mesh, kind, ids) for kind, ids in users.items() if ids}
    for n in doomed:
        del mesh.nodes[n]
    for values in mesh.nodal_data.values():
        for n in doomed:
            values.pop(n, None)
    for fixed in mesh.nodal_data_fixed.values():
        fixed -= doomed
    for values in mesh.vector_nodal_data.values():
        for n in doomed:
            values.pop(n, None)
    for var in list(mesh.nodal_flags):
        mesh.nodal_flags[var] = [n for n in mesh.nodal_flags[var] if n not in doomed]
        if not mesh.nodal_flags[var]:
            del mesh.nodal_flags[var]
    for part in _walk(mesh):
        part.nodes = [n for n in part.nodes if n not in doomed]
    return {"nodes": len(doomed), **{k: v for k, v in removed.items()}}


# ------------------------------------------------------------------- entities

def add_entities(mesh: Mdpa, kind: str, type_name: str, entities: list[dict[str, Any]],
                 part: str | None = None) -> list[int]:
    """Add entities of one type. Each item: ``{"nodes": [...], "id": optional,
    "property_id": optional (default 1)}``."""
    store = _store(mesh, kind).setdefault(type_name, {})
    expected = mdpa_mod.expected_node_count(type_name)
    taken = _all_ids(mesh, kind)
    next_id = _next_id(taken)
    new_ids: list[int] = []
    for index, item in enumerate(entities):
        nodes = [int(n) for n in item.get("nodes", [])]
        if not nodes:
            raise ValueError(f"entities[{index}] has no 'nodes'")
        missing = [n for n in nodes if n not in mesh.nodes]
        if missing:
            raise ValueError(f"entities[{index}] references missing nodes {missing}")
        if expected is not None and len(nodes) != expected:
            raise ValueError(f"entities[{index}] has {len(nodes)} nodes but {type_name} needs "
                             f"{expected}")
        eid = int(item["id"]) if item.get("id") is not None else next_id
        if eid in taken:
            raise ValueError(f"{kind.capitalize()} id {eid} already exists")
        taken.add(eid)
        next_id = max(next_id, eid + 1)
        store[eid] = nodes if kind == "geometry" else (int(item.get("property_id", 1)), nodes)
        new_ids.append(eid)
    if not store:
        del _store(mesh, kind)[type_name]
    if part and new_ids:
        add_members(mesh, part, **{kind + "s": new_ids})
    return new_ids


def delete_entities(mesh: Mdpa, kind: str, ids: Iterable[int]) -> int:
    doomed = {int(i) for i in ids}
    store = _store(mesh, kind)
    missing = sorted(doomed - _all_ids(mesh, kind))
    if missing:
        raise ValueError(f"Missing {kind}s {missing[:10]}")
    for type_name in list(store):
        for eid in [e for e in store[type_name] if e in doomed]:
            del store[type_name][eid]
        if not store[type_name]:
            del store[type_name]
    if kind == "element":
        for by_id in mesh.elemental_data.values():
            for eid in doomed:
                by_id.pop(eid, None)
    elif kind == "condition":
        for by_id in mesh.conditional_data.values():
            for cid in doomed:
                by_id.pop(cid, None)
    for part in _walk(mesh):
        members = _part_list(part, kind)
        members[:] = [m for m in members if m not in doomed]
    return len(doomed)


def set_entity_properties(mesh: Mdpa, kind: str, ids: Iterable[int],
                          property_id: int | None = None, new_type: str | None = None) -> int:
    """Change the property id and/or retype entities (the node count must match)."""
    if kind == "geometry" and property_id is not None:
        raise ValueError("geometries have no property id")
    if property_id is None and new_type is None:
        raise ValueError("give property_id and/or new_type")
    wanted = {int(i) for i in ids}
    missing = sorted(wanted - _all_ids(mesh, kind))
    if missing:
        raise ValueError(f"Missing {kind}s {missing[:10]}")
    store = _store(mesh, kind)
    expected = mdpa_mod.expected_node_count(new_type) if new_type else None
    moved = 0
    for type_name in list(store):
        for eid in [e for e in store[type_name] if e in wanted]:
            entry = store[type_name].pop(eid)
            conn = _conn(entry)
            if expected is not None and len(conn) != expected:
                raise ValueError(f"{kind.capitalize()} {eid} has {len(conn)} nodes, "
                                 f"{new_type} needs {expected}")
            target = store.setdefault(new_type or type_name, {})
            if kind == "geometry":
                target[eid] = conn
            else:
                target[eid] = (entry[0] if property_id is None else int(property_id), conn)
            moved += 1
        if not store.get(type_name):
            store.pop(type_name, None)
    return moved


# ------------------------------------------------------------- sub model parts

def create_part(mesh: Mdpa, path: str, nodes: Iterable[int] = (), elements: Iterable[int] = (),
                conditions: Iterable[int] = (), geometries: Iterable[int] = (),
                include: Iterable[str] = (), exist_ok: bool = False) -> SubModelPart:
    """Create a SubModelPart at the dotted ``path`` (the parent must exist).

    ``include`` lists entity kinds ('element', 'condition', 'geometry') whose
    members lying entirely on the part's nodes are added automatically."""
    *parents, name = path.split(".")
    if not name:
        raise ValueError("path is empty")
    siblings = mesh.sub_model_parts
    if parents:
        siblings = _require_part(mesh, ".".join(parents)).children
    if name in siblings and not exist_ok:
        raise ValueError(f"SubModelPart '{path}' already exists")
    part = siblings.setdefault(name, SubModelPart(name))
    add_members(mesh, path, nodes=nodes, elements=elements, conditions=conditions,
                geometries=geometries)
    for kind in include:
        _store(mesh, kind)  # validates the kind
        found = entities_on_nodes(mesh, kind, part.nodes)
        add_members(mesh, path, **{kind + "s": found})
    return part


def add_members(mesh: Mdpa, path: str, nodes: Iterable[int] = (), elements: Iterable[int] = (),
                conditions: Iterable[int] = (), geometries: Iterable[int] = ()) -> dict[str, int]:
    """Add members to a part (and, as Kratos does, to its ancestors). The nodes
    of added entities are added too."""
    part = _require_part(mesh, path)
    chain = [mesh.sub_model_parts[path.split(".")[0]]]
    for segment in path.split(".")[1:]:
        chain.append(chain[-1].children[segment])
    given = {"node": list(nodes), "element": list(elements), "condition": list(conditions),
             "geometry": list(geometries)}
    missing_nodes = [n for n in given["node"] if n not in mesh.nodes]
    if missing_nodes:
        raise ValueError(f"Missing nodes {missing_nodes[:10]}")
    extra_nodes: list[int] = []
    for kind in KINDS:
        known = _all_ids(mesh, kind)
        missing = [i for i in given[kind] if i not in known]
        if missing:
            raise ValueError(f"Missing {kind}s {missing[:10]}")
        wanted = set(given[kind])
        for by_id in _store(mesh, kind).values():
            for eid, entry in by_id.items():
                if eid in wanted:
                    extra_nodes += _conn(entry)
    added = {"nodes": 0, "elements": 0, "conditions": 0, "geometries": 0}
    for target in chain:
        for attr, ids in (("nodes", given["node"] + extra_nodes), ("elements", given["element"]),
                          ("conditions", given["condition"]), ("geometries", given["geometry"])):
            current = getattr(target, attr)
            have = set(current)
            fresh = [i for i in _dedupe(ids) if i not in have]
            current.extend(fresh)
            if target is part:
                added[attr] = len(fresh)
    return added


def remove_members(mesh: Mdpa, path: str, nodes: Iterable[int] = (), elements: Iterable[int] = (),
                   conditions: Iterable[int] = (), geometries: Iterable[int] = ()) -> None:
    """Remove members from a part and from its descendants (they cannot hold
    what the parent no longer has)."""
    part = _require_part(mesh, path)
    drop = {"nodes": set(nodes), "elements": set(elements), "conditions": set(conditions),
            "geometries": set(geometries)}

    def rec(p: SubModelPart) -> None:
        for attr, ids in drop.items():
            if ids:
                setattr(p, attr, [i for i in getattr(p, attr) if i not in ids])
        for child in p.children.values():
            rec(child)

    rec(part)


def delete_part(mesh: Mdpa, path: str) -> None:
    *parents, name = path.split(".")
    _require_part(mesh, path)
    siblings = _require_part(mesh, ".".join(parents)).children if parents else mesh.sub_model_parts
    del siblings[name]


def rename_part(mesh: Mdpa, path: str, new_name: str) -> str:
    if not new_name or "." in new_name or " " in new_name:
        raise ValueError("new_name must be a single word without dots or spaces")
    part = _require_part(mesh, path)
    *parents, name = path.split(".")
    siblings = _require_part(mesh, ".".join(parents)).children if parents else mesh.sub_model_parts
    if new_name in siblings:
        raise ValueError(f"A SubModelPart named '{new_name}' already exists there")
    part.name = new_name
    rebuilt = {(new_name if key == name else key): value for key, value in siblings.items()}
    siblings.clear()
    siblings.update(rebuilt)
    return ".".join(parents + [new_name])


# ----------------------------------------------------------------- transforms

def _rotation_matrix(axis: np.ndarray, degrees: float) -> np.ndarray:
    norm = float(np.linalg.norm(axis))
    if norm == 0:
        raise ValueError("rotation axis must be non-zero")
    x, y, z = axis / norm
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([
        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
    ])


def transform(mesh: Mdpa, operation: str, node_ids: Iterable[int] | None = None, *,
              vector: list[float] | None = None, angle: float | None = None,
              axis: list[float] | None = None, origin: list[float] | None = None,
              factors: list[float] | float | None = None,
              point: list[float] | None = None, normal: list[float] | None = None) -> dict[str, Any]:
    """Translate / rotate / scale / mirror the given nodes (all by default).

    translate: ``vector``; rotate: ``angle`` degrees about ``axis`` (default z)
    through ``origin``; scale: ``factors`` (one number or per axis) about
    ``origin``; mirror: across the plane through ``point`` with ``normal``.
    Orientation-reversing operations (mirror, negative scale) re-order the
    connectivity of elements/conditions (including quadratic mid-edge and face
    nodes) so they keep a positive measure."""
    ids = sorted(mesh.nodes) if node_ids is None else [int(n) for n in node_ids]
    missing = [n for n in ids if n not in mesh.nodes]
    if missing:
        raise ValueError(f"Missing nodes {missing[:10]}")
    if not ids:
        return {"nodes": 0}
    pts = np.array([mesh.nodes[i] for i in ids])
    org = _vec3(origin if origin is not None else [0, 0, 0], "origin")
    flips = False
    if operation == "translate":
        if vector is None:
            raise ValueError("translate needs 'vector'")
        pts = pts + _vec3(vector, "vector")
    elif operation == "rotate":
        if angle is None:
            raise ValueError("rotate needs 'angle' (degrees)")
        rot = _rotation_matrix(_vec3(axis if axis is not None else [0, 0, 1], "axis"), angle)
        pts = (pts - org) @ rot.T + org
    elif operation == "scale":
        if factors is None:
            raise ValueError("scale needs 'factors'")
        f = np.full(3, float(factors)) if isinstance(factors, (int, float)) \
            else _vec3(factors, "factors", 1.0)
        if np.any(f == 0):
            raise ValueError("scale factors must be non-zero")
        pts = (pts - org) * f + org
        flips = int(np.sum(f < 0)) % 2 == 1
    elif operation == "mirror":
        if point is None or normal is None:
            raise ValueError("mirror needs 'point' and 'normal'")
        n = _vec3(normal, "normal")
        length = float(np.linalg.norm(n))
        if length == 0:
            raise ValueError("normal must be non-zero")
        n = n / length
        p0 = _vec3(point, "point")
        pts = pts - 2.0 * ((pts - p0) @ n)[:, None] * n
        flips = True
    else:
        raise ValueError("operation must be translate, rotate, scale or mirror")
    for nid, xyz in zip(ids, pts):
        mesh.nodes[nid] = tuple(float(v) for v in xyz)
    reoriented = 0
    if flips:
        moved = set(ids)
        for kind in ("element", "condition"):
            for type_name, by_id in _store(mesh, kind).items():
                for eid, (pid, conn) in by_id.items():
                    if moved.issuperset(conn):
                        new = _flip_connectivity(mesh, type_name, conn)
                        if new != conn:
                            by_id[eid] = (pid, new)
                            reoriented += 1
    return {"nodes": len(ids), "reoriented_entities": reoriented}


_CORNER_FLIP = {
    "tri": (0, 2, 1),
    "quad": (0, 3, 2, 1),
    "tet": (0, 2, 1, 3),
    "hex": (0, 3, 2, 1, 4, 7, 6, 5),
    "prism": (0, 2, 1, 3, 5, 4),
    "pyr": (0, 3, 2, 1, 4),
}


def _flip_connectivity(mesh: Mdpa, type_name: str, conn: list[int]) -> list[int]:
    kind = cell_kind(mesh, type_name, conn)
    n = len(conn)
    c = list(conn)
    if kind is None or kind == "line":
        return c
    corners = _CORNERS.get((kind, n), tuple(range(_LINEAR_COUNTS[kind])))
    perm = _CORNER_FLIP[kind]
    if len(perm) != len(corners):
        return c
    new: list[int | None] = [None] * n
    for new_pos, old_pos in enumerate(perm):
        new[corners[new_pos]] = c[corners[old_pos]]
    pos_of = {old: new_pos for new_pos, old in enumerate(perm)}
    for a, b, slots in _edges_with_slots(kind, n):
        targets = _edge_slots(kind, n, pos_of[a], pos_of[b])
        if targets is None or len(targets) != len(slots):
            return c
        for target, slot in zip(targets, slots):
            new[target] = c[slot]
    centers = _FACE_CENTERS.get((kind, n))
    if centers is not None:
        faces = FACES[kind]
        for f_idx, face in enumerate(faces):
            if centers[f_idx] is None:
                continue
            # Old-corner set now sitting on new face f_idx; take its old center.
            wanted = frozenset(perm[o] for o in face)
            g = next((g for g, old in enumerate(faces) if frozenset(old) == wanted), None)
            if g is None or centers[g] is None:
                return c
            new[centers[f_idx]] = c[centers[g]]
    around = _CORNER_INTERIOR.get((kind, n))
    if around is not None:
        for new_pos, old_pos in enumerate(perm):
            new[around[new_pos]] = c[around[old_pos]]
    for slot in _FIXED_SLOTS.get((kind, n), ()):
        new[slot] = c[slot]
    if any(v is None for v in new):
        return c
    return [int(v) for v in new]


# --------------------------------------------------------------- cell geometry

# Edges / outward faces / measure decompositions per linear cell kind, in
# Kratos node order.
EDGES = {
    "line": [(0, 1)],
    "tri": [(0, 1), (1, 2), (2, 0)],
    "quad": [(0, 1), (1, 2), (2, 3), (3, 0)],
    "tet": [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)],
    "prism": [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)],
    "hex": [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7)],
    "pyr": [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)],
}
FACES = {
    "tri": [(0, 1), (1, 2), (2, 0)],
    "quad": [(0, 1), (1, 2), (2, 3), (3, 0)],
    "tet": [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)],
    "hex": [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    "prism": [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
    # base quad first, then the four triangles around the apex (node 4)
    "pyr": [(0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)],
}
_TETS_OF = {
    "tet": [(0, 1, 2, 3)],
    "prism": [(0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5)],
    "hex": [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)],
    "pyr": [(0, 1, 2, 4), (0, 2, 3, 4)],
}


def cell_kind(mesh: Mdpa, type_name: str, conn: list[int]) -> str | None:
    """Cell kind of an entity: line, tri, quad, tet, pyr, prism, hex (None if it is
    not one of them). Higher-order (quadratic and cubic) cells map to their linear kind:
    their corners come first in Kratos ordering, so measures use the corners.
    A 3-node entity is a quadratic line when its name says so (Line, Truss,
    Bar, Beam, Edge, Cable) or when its nodes are collinear, else a triangle.
    A 4-node entity is a tet when its nodes span a volume and a quad when they
    are planar. A 6-node entity is a quadratic triangle for ``2D6``/``Tri``
    names, else a prism; an 8-node entity a quadratic quad for ``2D8``/``Quad``
    names, else a hexahedron. A 10-node entity is a cubic triangle for
    ``2D10``/``Tri`` names, else a quadratic tetrahedron; a 4-node entity named
    like a line (Line, Truss, ...) is a cubic line; 16 nodes are a cubic
    quadrilateral and 5, 13 or 14 nodes a (quadratic) pyramid."""
    n = len(conn)
    if n == 2:
        return "line"
    if n == 3:
        if any(s in type_name for s in ("Line", "Truss", "Bar", "Beam", "Edge", "Cable")):
            return "line"
        pts = [mesh.nodes.get(i) for i in conn]
        if all(p is not None for p in pts):
            a, b, c = (np.array(p) for p in pts)
            scale = max(float(np.linalg.norm(b - a)), float(np.linalg.norm(c - a)), 1e-300)
            if float(np.linalg.norm(np.cross(b - a, c - a))) / scale ** 2 < 1e-9:
                return "line"
        return "tri"
    if n == 6:
        if "2D6" in type_name or "Tri" in type_name:
            return "tri"
        return "prism"
    if n == 8:
        if "2D8" in type_name or "Quad" in type_name:
            return "quad"
        return "hex"
    if n == 9:
        return "quad"
    if n == 10:
        if "3D10" not in type_name and ("2D10" in type_name or "Tri" in type_name):
            return "tri"
        return "tet"
    if n == 16:
        return "quad"
    if n in (13, 14):
        return "pyr"
    if n == 15:
        return "prism"
    if n in (20, 27):
        return "hex"
    if n == 4 and any(s in type_name for s in ("Line", "Truss", "Bar", "Beam", "Edge", "Cable")):
        return "line"
    if n == 5:
        return "pyr"
    if n == 4:
        p = np.array([mesh.nodes[i] for i in conn])
        scale = max(float(np.ptp(p, axis=0).max()), 1e-300)
        vol = abs(float(np.linalg.det(p[1:] - p[0]))) / scale ** 3
        return "tet" if vol > 1e-9 else "quad"
    return None


# Corner-node indices of quadratic cells in Kratos ordering (linear cells use
# every node). Measures, edge lengths and boundary faces use the corners.
_CORNERS: dict[tuple[str, int], tuple[int, ...]] = {
    ("line", 3): (0, 1),
    ("tri", 6): (0, 1, 2),
    ("quad", 8): (0, 1, 2, 3),
    ("quad", 9): (0, 1, 2, 3),
    ("tet", 10): (0, 1, 2, 3),
    ("hex", 20): tuple(range(8)),
    ("hex", 27): tuple(range(8)),
    ("prism", 15): tuple(range(6)),
    ("line", 4): (0, 1),
    ("tri", 10): (0, 1, 2),
    ("quad", 16): (0, 1, 2, 3),
    ("pyr", 13): tuple(range(5)),
    ("pyr", 14): tuple(range(5)),
}

# Corner-node count of the linear cell of each kind.
_LINEAR_COUNTS = {"line": 2, "tri": 3, "quad": 4, "tet": 4, "hex": 8, "prism": 6, "pyr": 5}

# Edge-midpoint slots of quadratic cells: frozenset of corner positions -> the
# connectivity index holding the midpoint. Verified against Kratos'
# GenerateEdges (Triangle2D6, Quadrilateral2D8/9, Tetrahedra3D10,
# Hexahedra3D20/27, Prism3D15, Line2D3).
_EDGE_MIDS: dict[tuple[str, int], dict[frozenset, int]] = {
    ("line", 3): {frozenset((0, 1)): 2},
    ("tri", 6): {frozenset((0, 1)): 3, frozenset((1, 2)): 4, frozenset((2, 0)): 5},
    ("quad", 8): {frozenset((0, 1)): 4, frozenset((1, 2)): 5,
                  frozenset((2, 3)): 6, frozenset((3, 0)): 7},
    ("quad", 9): {frozenset((0, 1)): 4, frozenset((1, 2)): 5,
                  frozenset((2, 3)): 6, frozenset((3, 0)): 7},
    ("tet", 10): {frozenset((0, 1)): 4, frozenset((1, 2)): 5, frozenset((2, 0)): 6,
                  frozenset((0, 3)): 7, frozenset((1, 3)): 8, frozenset((2, 3)): 9},
    ("hex", 20): {frozenset((0, 1)): 8, frozenset((1, 2)): 9, frozenset((2, 3)): 10,
                  frozenset((3, 0)): 11, frozenset((4, 5)): 16, frozenset((5, 6)): 17,
                  frozenset((6, 7)): 18, frozenset((7, 4)): 19, frozenset((0, 4)): 12,
                  frozenset((1, 5)): 13, frozenset((2, 6)): 14, frozenset((3, 7)): 15},
    ("hex", 27): {frozenset((0, 1)): 8, frozenset((1, 2)): 9, frozenset((2, 3)): 10,
                  frozenset((3, 0)): 11, frozenset((4, 5)): 16, frozenset((5, 6)): 17,
                  frozenset((6, 7)): 18, frozenset((7, 4)): 19, frozenset((0, 4)): 12,
                  frozenset((1, 5)): 13, frozenset((2, 6)): 14, frozenset((3, 7)): 15},
    ("prism", 15): {frozenset((0, 1)): 6, frozenset((1, 2)): 7, frozenset((2, 0)): 8,
                    frozenset((3, 4)): 12, frozenset((4, 5)): 13, frozenset((5, 3)): 14,
                    frozenset((0, 3)): 9, frozenset((1, 4)): 10, frozenset((2, 5)): 11},
}

# Edge nodes of cells with SEVERAL nodes per edge (cubic) or whose edges are
# listed here in a fixed direction: (corner a, corner b) -> connectivity slots in
# order from a to b. Lagrange (Gmsh / meshio / VTK) node order; NOT cross-checked
# against Kratos' GenerateEdges (no Kratos source in this repo's CI), unlike the
# quadratic tables above. Pyramid13/14 follow the same convention as Kratos'
# Pyramid3D13 (base edges, then edges to the apex).
_EDGE_NODES: dict[tuple[str, int], dict[tuple[int, int], list[int]]] = {
    ("line", 4): {(0, 1): [2, 3]},
    ("tri", 10): {(0, 1): [3, 4], (1, 2): [5, 6], (2, 0): [7, 8]},
    ("quad", 16): {(0, 1): [4, 5], (1, 2): [6, 7], (2, 3): [8, 9], (3, 0): [10, 11]},
    ("pyr", 13): {(0, 1): [5], (1, 2): [6], (2, 3): [7], (3, 0): [8],
                  (0, 4): [9], (1, 4): [10], (2, 4): [11], (3, 4): [12]},
    ("pyr", 14): {(0, 1): [5], (1, 2): [6], (2, 3): [7], (3, 0): [8],
                  (0, 4): [9], (1, 4): [10], (2, 4): [11], (3, 4): [12]},
}

# Interior nodes sitting next to each corner (quad16: Gmsh order, node 12+i near
# corner i); they permute with the corners under an orientation flip.
_CORNER_INTERIOR: dict[tuple[str, int], tuple[int, ...]] = {
    ("quad", 16): (12, 13, 14, 15),
}


def _edge_slots(kind: str, n: int, a: int, b: int) -> list[int] | None:
    """Connectivity slots of the nodes between corners ``a`` and ``b`` ordered from
    ``a`` to ``b`` (None when ``(kind, n)`` has no such edge)."""
    table = _EDGE_NODES.get((kind, n))
    if table is not None:
        if (a, b) in table:
            return list(table[(a, b)])
        if (b, a) in table:
            return list(reversed(table[(b, a)]))
        return None
    slot = _EDGE_MIDS.get((kind, n), {}).get(frozenset((a, b)))
    return None if slot is None else [slot]


def _edges_with_slots(kind: str, n: int) -> list[tuple[int, int, list[int]]]:
    table = _EDGE_NODES.get((kind, n))
    if table is not None:
        return [(a, b, list(slots)) for (a, b), slots in table.items()]
    return [(a, b, [slot]) for edge, slot in _EDGE_MIDS.get((kind, n), {}).items()
            for a, b in [tuple(edge)]]


# Face-center slots of quadratic cells with face nodes, aligned with the
# FACES tables below (verified against Kratos' GenerateFaces).
_FACE_CENTERS: dict[tuple[str, int], list[int | None]] = {
    ("hex", 27): [20, 25, 21, 22, 23, 24],
    ("pyr", 14): [13, None, None, None, None],
}

# Slots that stay in place under an orientation flip (volume / face centers
# without an oriented position).
_FIXED_SLOTS: dict[tuple[str, int], tuple[int, ...]] = {
    ("hex", 27): (26,),
    ("quad", 9): (8,),
    ("tri", 10): (9,),
}


def corner_nodes(mesh: Mdpa, type_name: str, conn: list[int]) -> list[int]:
    """Corner node ids of an entity (every node for linear cells)."""
    kind = cell_kind(mesh, type_name, conn)
    if kind is None:
        return list(conn)
    idx = _CORNERS.get((kind, len(conn)))
    if idx is None:
        return list(conn)
    return [conn[i] for i in idx]


def is_higher_order(mesh: Mdpa, type_name: str, conn: list[int]) -> bool:
    """True when the entity carries mid-edge (or face) nodes past the corners."""
    kind = cell_kind(mesh, type_name, conn)
    return kind is not None and len(conn) != _LINEAR_COUNTS[kind]


def full_face_nodes(kind: str, n: int, corners: tuple[int, ...], face_index: int) -> list[int]:
    """Connectivity indices of one face: the outward-ordered ``corners`` loop,
    then the nodes along each edge in loop order (one per edge for quadratic
    cells, two for cubic ones), then its face center (if any)."""
    full = list(corners)
    pairs = [(corners[0], corners[1])] if len(corners) == 2 else \
        list(zip(corners, corners[1:] + corners[:1]))
    for a, b in pairs:
        full.extend(_edge_slots(kind, n, a, b) or [])
    centers = _FACE_CENTERS.get((kind, n))
    if centers is not None and centers[face_index] is not None:
        full.append(centers[face_index])
    return full


def _signed_tet(p: np.ndarray) -> float:
    return float(np.linalg.det(p[1:] - p[0])) / 6.0


def measure(mesh: Mdpa, type_name: str, conn: list[int]) -> float | None:
    """Length / area / volume of a cell from its corner nodes (higher-order
    cells use the corners); negative when inverted (areas are signed only for
    cells lying in a constant-z plane)."""
    kind = cell_kind(mesh, type_name, conn)
    if kind is None:
        return None
    corners = corner_nodes(mesh, type_name, conn)
    p = np.array([mesh.nodes[i] for i in corners])
    if kind == "line":
        return float(np.linalg.norm(p[1] - p[0]))
    if kind in ("tri", "quad"):
        planar_xy = bool(np.allclose(p[:, 2], p[0, 2]))
        tris = [(0, 1, 2)] if kind == "tri" else [(0, 1, 2), (0, 2, 3)]
        area = 0.0
        for a, b, c in tris:
            cross = np.cross(p[b] - p[a], p[c] - p[a])
            area += cross[2] / 2.0 if planar_xy else float(np.linalg.norm(cross)) / 2.0
        return float(area)
    return float(sum(_signed_tet(p[list(t)]) for t in _TETS_OF[kind]))


def edge_lengths(mesh: Mdpa, type_name: str, conn: list[int]) -> list[float]:
    kind = cell_kind(mesh, type_name, conn)
    if kind is None:
        return []
    corners = corner_nodes(mesh, type_name, conn)
    return [float(np.linalg.norm(np.array(mesh.nodes[corners[a]]) - np.array(mesh.nodes[corners[b]])))
            for a, b in EDGES[kind]]


def quality(mesh: Mdpa, worst: int = 5) -> dict[str, Any]:
    """Per-type measure statistics, edge-length range, aspect-ratio
    (longest/shortest edge) extremes and non-positive (inverted/degenerate) cells."""
    report: dict[str, Any] = {}
    for label, store in (("elements", mesh.elements), ("conditions", mesh.conditions)):
        by_type: dict[str, Any] = {}
        for type_name, by_id in store.items():
            measures, aspects, bad, skipped = [], [], [], 0
            sized: list[tuple[int, float]] = []
            for eid, (_, conn) in by_id.items():
                if any(n not in mesh.nodes for n in conn):
                    bad.append(eid)
                    continue
                m = measure(mesh, type_name, conn)
                if m is None:
                    skipped += 1
                    continue
                measures.append(m)
                sized.append((eid, m))
                lengths = edge_lengths(mesh, type_name, conn)
                if lengths and min(lengths) > 0:
                    aspects.append((max(lengths) / min(lengths), eid))
            scale = max((abs(m) for m in measures), default=0.0)
            bad += [eid for eid, m in sized if m <= 1e-12 * scale]
            entry: dict[str, Any] = {"count": len(by_id)}
            if measures:
                entry["measure"] = {"min": min(measures), "max": max(measures),
                                    "mean": sum(measures) / len(measures),
                                    "total": sum(measures)}
            if aspects:
                aspects.sort(reverse=True)
                entry["aspect_ratio"] = {"max": aspects[0][0],
                                         "mean": sum(a for a, _ in aspects) / len(aspects),
                                         "worst_ids": [e for _, e in aspects[:worst]]}
            if bad:
                entry["non_positive_or_invalid"] = {"count": len(bad), "ids": sorted(bad)[:20]}
            if skipped:
                entry["skipped_unsupported"] = skipped
            by_type[type_name] = entry
        report[label] = by_type
    return report


# ------------------------------------------------------- merge / renumber / etc

def merge_coincident_nodes(mesh: Mdpa, tolerance: float, node_ids: Iterable[int] | None = None
                           ) -> dict[str, int]:
    """Merge nodes closer than ``tolerance`` (lowest id survives) and rewrite
    every reference. Degenerate entities (repeated node) are reported, not removed."""
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    ids = sorted(mesh.nodes) if node_ids is None else sorted(int(n) for n in node_ids)
    cell = tolerance
    grid: dict[tuple[int, int, int], list[int]] = {}
    replace: dict[int, int] = {}
    for nid in ids:
        x, y, z = mesh.nodes[nid]
        key = (math.floor(x / cell), math.floor(y / cell), math.floor(z / cell))
        found = None
        for off in itertools.product((-1, 0, 1), repeat=3):
            for other in grid.get((key[0] + off[0], key[1] + off[1], key[2] + off[2]), ()):
                if math.dist(mesh.nodes[other], (x, y, z)) <= tolerance:
                    found = other
                    break
            if found is not None:
                break
        if found is None:
            grid.setdefault(key, []).append(nid)
        else:
            replace[nid] = found
    if not replace:
        return {"merged": 0, "degenerate_entities": 0}
    degenerate = 0
    for kind in KINDS:
        for type_name, by_id in _store(mesh, kind).items():
            for eid, entry in list(by_id.items()):
                conn = [replace.get(n, n) for n in _conn(entry)]
                if len(set(conn)) != len(conn):
                    degenerate += 1
                by_id[eid] = conn if kind == "geometry" else (entry[0], conn)
    for nid in replace:
        del mesh.nodes[nid]
    for values in mesh.nodal_data.values():
        for nid in replace:
            values.pop(nid, None)
    for fixed in mesh.nodal_data_fixed.values():
        fixed -= set(replace)
    for values in mesh.vector_nodal_data.values():
        for nid in replace:
            values.pop(nid, None)
    for var in list(mesh.nodal_flags):
        mesh.nodal_flags[var] = [n for n in mesh.nodal_flags[var] if n not in replace]
        if not mesh.nodal_flags[var]:
            del mesh.nodal_flags[var]
    for part in _walk(mesh):
        part.nodes = _dedupe(replace.get(n, n) for n in part.nodes)
    return {"merged": len(replace), "degenerate_entities": degenerate}


def _remap_everywhere(mesh: Mdpa, node_map: dict[int, int], ent_maps: dict[str, dict[int, int]]
                      ) -> None:
    mesh.nodes = {node_map[n]: xyz for n, xyz in mesh.nodes.items()}
    mesh.nodal_data = {var: {node_map[n]: v for n, v in values.items() if n in node_map}
                       for var, values in mesh.nodal_data.items()}
    mesh.nodal_data_fixed = {
        var: {node_map[n] for n in fixed if n in node_map}
        for var, fixed in mesh.nodal_data_fixed.items()}
    mesh.vector_nodal_data = {
        var: {node_map[n]: v for n, v in values.items() if n in node_map}
        for var, values in mesh.vector_nodal_data.items()}
    mesh.nodal_flags = {
        var: sorted(node_map[n] for n in ids if n in node_map)
        for var, ids in mesh.nodal_flags.items()}
    mesh.elemental_data = {
        var: {ent_maps["element"][e]: v for e, v in by_id.items() if e in ent_maps["element"]}
        for var, by_id in mesh.elemental_data.items()}
    mesh.conditional_data = {
        var: {ent_maps["condition"][c]: v for c, v in by_id.items()
              if c in ent_maps["condition"]}
        for var, by_id in mesh.conditional_data.items()}
    for kind in KINDS:
        remap = ent_maps[kind]
        store = _store(mesh, kind)
        for type_name, by_id in list(store.items()):
            new: dict[int, Any] = {}
            for eid, entry in by_id.items():
                conn = [node_map[n] for n in _conn(entry)]
                new[remap[eid]] = conn if kind == "geometry" else (entry[0], conn)
            store[type_name] = new
    for part in _walk(mesh):
        part.nodes = [node_map[n] for n in part.nodes]
        for kind in KINDS:
            members = _part_list(part, kind)
            members[:] = [ent_maps[kind][m] for m in members]


def renumber(mesh: Mdpa, start: int = 1) -> dict[str, dict[str, int]]:
    """Renumber nodes, elements, conditions and geometries consecutively from
    ``start`` (in type-name then id order, which is how the file is written)."""
    node_map = {n: start + i for i, n in enumerate(sorted(mesh.nodes))}
    ent_maps: dict[str, dict[int, int]] = {}
    for kind in KINDS:
        order = [eid for type_name in sorted(_store(mesh, kind))
                 for eid in sorted(_store(mesh, kind)[type_name])]
        ent_maps[kind] = {eid: start + i for i, eid in enumerate(order)}
    _remap_everywhere(mesh, node_map, ent_maps)
    return {"nodes": {"first": start, "last": start + len(node_map) - 1},
            **{kind + "s": {"first": start, "last": start + len(m) - 1} if m else {}
               for kind, m in ent_maps.items()}}


def merge_meshes(base: Mdpa, other: Mdpa, merge_tolerance: float | None = None
                 ) -> dict[str, Any]:
    """Append ``other`` to ``base`` (ids of ``other`` are shifted past base's).
    Sub model parts with the same name are united. With ``merge_tolerance``
    coincident nodes (e.g. along a shared interface) are then merged."""
    n_off = max(base.nodes, default=0)
    e_off = max(base.element_ids(), default=0)
    c_off = max(base.condition_ids(), default=0)
    g_off = max(base.geometry_ids(), default=0)
    for nid, xyz in other.nodes.items():
        base.nodes[nid + n_off] = xyz
    for var, values in other.nodal_data.items():
        base.nodal_data.setdefault(var, {}).update({n + n_off: v for n, v in values.items()})
    for var, fixed in other.nodal_data_fixed.items():
        base.nodal_data_fixed.setdefault(var, set()).update(n + n_off for n in fixed)
    for var, values in other.vector_nodal_data.items():
        base.vector_nodal_data.setdefault(var, {}).update(
            {n + n_off: v for n, v in values.items()})
    for var, ids in other.nodal_flags.items():
        base.nodal_flags.setdefault(var, []).extend(n + n_off for n in ids)
    table_map: dict[int, int] = {}
    if other.tables:
        if set(other.tables) & set(base.tables):
            shift = max(base.tables) + 1 - min(other.tables)
            for tid, tab in other.tables.items():
                new_id = tid + shift
                base.tables[new_id] = mdpa_mod.Table(
                    new_id, tab.x_variable, tab.y_variable, list(tab.rows))
                table_map[tid] = new_id
        else:
            for tid, tab in other.tables.items():
                base.tables[tid] = mdpa_mod.Table(
                    tid, tab.x_variable, tab.y_variable, list(tab.rows))
    for var, by_id in other.elemental_data.items():
        base.elemental_data.setdefault(var, {}).update(
            {e + e_off: v for e, v in by_id.items()})
    for var, by_id in other.conditional_data.items():
        base.conditional_data.setdefault(var, {}).update(
            {c + c_off: v for c, v in by_id.items()})
    for kind, off in (("element", e_off), ("condition", c_off), ("geometry", g_off)):
        for type_name, by_id in _store(other, kind).items():
            target = _store(base, kind).setdefault(type_name, {})
            for eid, entry in by_id.items():
                conn = [n + n_off for n in _conn(entry)]
                target[eid + off] = conn if kind == "geometry" else (entry[0], conn)
    for pid, props in other.properties.items():
        base.properties.setdefault(pid, dict(props))

    def merge_part(src: SubModelPart, siblings: dict[str, SubModelPart]) -> None:
        dst = siblings.setdefault(src.name, SubModelPart(src.name))
        dst.nodes = _dedupe(dst.nodes + [n + n_off for n in src.nodes])
        dst.elements = _dedupe(dst.elements + [e + e_off for e in src.elements])
        dst.conditions = _dedupe(dst.conditions + [c + c_off for c in src.conditions])
        dst.geometries = _dedupe(dst.geometries + [g + g_off for g in src.geometries])
        dst.tables = _dedupe(dst.tables + [table_map.get(t, t) for t in src.tables])
        dst.properties = _dedupe(dst.properties + list(src.properties))
        for key, value in src.data.items():
            dst.data.setdefault(key, value)
        for child in src.children.values():
            merge_part(child, dst.children)

    for part in other.sub_model_parts.values():
        merge_part(part, base.sub_model_parts)
    result: dict[str, Any] = {"node_offset": n_off, "element_offset": e_off,
                              "condition_offset": c_off}
    if merge_tolerance:
        result["merge"] = merge_coincident_nodes(base, merge_tolerance)
    return result


def extract_part(mesh: Mdpa, path: str) -> Mdpa:
    """A new mesh holding only the part's entities and the nodes they use (and
    the part's own nodes), with the part's children kept and the part as root."""
    part = _require_part(mesh, path)
    out = Mdpa(model_part_data=dict(mesh.model_part_data))
    node_ids = set(part.nodes)
    for kind, attr in (("element", "elements"), ("condition", "conditions"),
                       ("geometry", "geometries")):
        wanted = set(getattr(part, attr))
        for type_name, by_id in _store(mesh, kind).items():
            chosen = {e: v for e, v in by_id.items() if e in wanted}
            if chosen:
                _store(out, kind)[type_name] = dict(chosen)
                for entry in chosen.values():
                    node_ids.update(_conn(entry))
    out.nodes = {n: mesh.nodes[n] for n in sorted(node_ids) if n in mesh.nodes}
    out.nodal_data = {var: {n: v for n, v in values.items() if n in out.nodes}
                      for var, values in mesh.nodal_data.items()}
    out.nodal_data_fixed = {var: {n for n in fixed if n in out.nodes}
                            for var, fixed in mesh.nodal_data_fixed.items()}
    out.vector_nodal_data = {var: {n: v for n, v in values.items() if n in out.nodes}
                             for var, values in mesh.vector_nodal_data.items()}
    out.nodal_flags = {var: [n for n in ids if n in out.nodes]
                       for var, ids in mesh.nodal_flags.items()}
    kept_elements = {e for by_id in out.elements.values() for e in by_id}
    kept_conditions = {c for by_id in out.conditions.values() for c in by_id}
    out.elemental_data = {
        var: {e: v for e, v in by_id.items() if e in kept_elements}
        for var, by_id in mesh.elemental_data.items()}
    out.conditional_data = {
        var: {c: v for c, v in by_id.items() if c in kept_conditions}
        for var, by_id in mesh.conditional_data.items()}
    pids = {entry[0] for store in (out.elements, out.conditions)
            for by_id in store.values() for entry in by_id.values()}
    out.properties = {pid: dict(mesh.properties[pid]) for pid in pids if pid in mesh.properties}

    def copy(src: SubModelPart) -> SubModelPart:
        dst = SubModelPart(src.name, nodes=list(src.nodes), elements=list(src.elements),
                           conditions=list(src.conditions), geometries=list(src.geometries),
                           data=dict(src.data), tables=list(src.tables),
                           properties=list(src.properties))
        dst.children = {n: copy(c) for n, c in src.children.items()}
        return dst

    out.sub_model_parts[part.name] = copy(part)
    kept_tables: set[int] = set()

    def collect_tables(src: SubModelPart) -> None:
        kept_tables.update(src.tables)
        for child in src.children.values():
            collect_tables(child)

    collect_tables(out.sub_model_parts[part.name])
    out.tables = {tid: tab for tid, tab in mesh.tables.items() if tid in kept_tables}
    return out


def set_nodal_data(mesh: Mdpa, variable: str, node_ids: Iterable[int],
                   value: float | str, fixed: bool = False) -> int:
    """Write a scalar (float) or vector (raw string like ``"[3](1.0, 0.0, 0.0)"``)
    ``Begin NodalData`` value on the given nodes."""
    ids = [int(n) for n in node_ids]
    missing = [n for n in ids if n not in mesh.nodes]
    if missing:
        raise ValueError(f"Missing nodes {missing[:10]}")
    if isinstance(value, str):
        mesh.vector_nodal_data.setdefault(variable, {}).update(
            {n: (fixed, value) for n in ids})
    else:
        mesh.nodal_data.setdefault(variable, {}).update({n: float(value) for n in ids})
        if fixed:
            mesh.nodal_data_fixed.setdefault(variable, set()).update(ids)
        elif variable in mesh.nodal_data_fixed:
            mesh.nodal_data_fixed[variable] -= set(ids)
    return len(ids)


def set_elemental_data(mesh: Mdpa, kind: str, variable: str, values: dict[int, Any],
                       remove_ids: Iterable[int] = ()) -> dict[str, int]:
    """Upsert ``Begin ElementalData`` (kind ``"elemental"``) or
    ``Begin ConditionalData`` (kind ``"conditional"``) rows. Values may be
    numbers or raw strings (``"[3](...)"`` vectors are kept verbatim)."""
    if kind == "elemental":
        target, known = mesh.elemental_data, mesh.element_ids()
    elif kind == "conditional":
        target, known = mesh.conditional_data, mesh.condition_ids()
    else:
        raise ValueError("kind must be 'elemental' or 'conditional'")
    bucket = target.setdefault(variable, {})
    updated = 0
    for eid, value in values.items():
        eid = int(eid)
        if eid not in known:
            raise ValueError(f"Missing {'element' if kind == 'elemental' else 'condition'} {eid}")
        bucket[eid] = str(value) if isinstance(value, (int, float)) \
            else " ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
        updated += 1
    removed = 0
    for eid in remove_ids:
        if bucket.pop(int(eid), None) is not None:
            removed += 1
    if not bucket:
        del target[variable]
    return {"updated": updated, "removed": removed}


def set_table(mesh: Mdpa, table_id: int, x_variable: str, y_variable: str,
              rows: list[list[float]]) -> dict[str, Any]:
    """Create or replace a top-level ``Begin Table`` block."""
    pairs = [(float(x), float(y)) for x, y in rows]
    mesh.tables[int(table_id)] = mdpa_mod.Table(int(table_id), x_variable, y_variable, pairs)
    return {"table": int(table_id), "rows": len(pairs)}


def delete_table(mesh: Mdpa, table_id: int) -> None:
    tid = int(table_id)
    if tid not in mesh.tables:
        raise ValueError(f"Missing table {tid}")
    users = [path for path in mdpa_mod.part_paths(mesh)
             if tid in (mdpa_mod.find_part(mesh, path).tables or ())]
    if users:
        raise ValueError(f"Table {tid} is referenced by SubModelPart(s) {users}; "
                         "remove the references first")
    del mesh.tables[tid]


def set_properties(mesh: Mdpa, property_id: int, values: dict[str, Any] | None = None,
                   remove: Iterable[str] = ()) -> dict[str, str]:
    props = mesh.properties.setdefault(int(property_id), {})
    for key, val in (values or {}).items():
        props[str(key)] = (" ".join(map(str, val)) if isinstance(val, (list, tuple)) else str(val))
    for key in remove:
        props.pop(key, None)
    return dict(props)


# ------------------------------------------------------- cell conversion, faces

_QUAD_TO_TRI = ((0, 1, 2), (0, 2, 3))


def _rename(name: str, old: str, new: str) -> str:
    return name.replace(old, new) if old in name else name


def _linear_name(name: str, n_corners: int) -> str:
    """Type name of the linear cell with ``n_corners`` corners (``2D6N``->``2D3N``,
    ``3D20N``->``3D8N``, ``Line2D3``->``Line2D2``)."""
    renamed = re.sub(r"(2D|3D)\d+(N?)$", lambda m: f"{m.group(1)}{n_corners}{m.group(2)}", name)
    return renamed if renamed != name else f"{name}_linear{n_corners}"


def _convert_quadratic_to_linear(mesh: Mdpa, element_name: str | None,
                                 condition_name: str | None) -> dict[str, int]:
    converted = {"elements": 0, "conditions": 0, "geometries": 0}
    for label, store, override in (("elements", mesh.elements, element_name),
                                   ("conditions", mesh.conditions, condition_name),
                                   ("geometries", mesh.geometries, None)):
        for type_name in list(store):
            by_id = store[type_name]
            targets = [eid for eid, entry in by_id.items()
                       if is_higher_order(mesh, type_name, _conn(entry))]
            if not targets:
                continue
            sample = corner_nodes(mesh, type_name, _conn(by_id[targets[0]]))
            new_type = override or _linear_name(type_name, len(sample))
            bucket = store.setdefault(new_type, {})
            for eid in sorted(targets):
                entry = by_id.pop(eid)
                corners = corner_nodes(mesh, type_name, _conn(entry))
                bucket[eid] = (entry[0], corners) if isinstance(entry, tuple) else corners
                converted[label] += 1
            if not by_id:
                del store[type_name]
    return converted


def convert_cells(mesh: Mdpa, mode: str, element_name: str | None = None,
                  condition_name: str | None = None) -> dict[str, int]:
    """Split quadrilaterals into 2 triangles (``quad_to_tri``) or hexahedra into
    6 tetrahedra (``hex_to_tet``, Kuhn split: conforming on structured meshes
    with a consistent node ordering; boundary quads follow the tet faces), or
    drop the mid-edge/face nodes of quadratic and cubic cells (``quadratic_to_linear``:
    tri6/tri10->tri, quad8/9/16->quad, tet10->tet, hex20/27->hex, wedge15->prism,
    pyramid13/14->pyramid, line3/line4->line; entity ids and sub model parts are
    unchanged).

    Entities of the other cell kinds are untouched. New type names default to the
    old ones with ``2D4N``->``2D3N`` / ``3D8N``->``3D4N`` (``3D4N`` -> ``3D3N``
    for conditions); pass element_name / condition_name to override."""
    if mode == "quadratic_to_linear":
        return _convert_quadratic_to_linear(mesh, element_name, condition_name)
    if mode == "quad_to_tri":
        src_kind, e_old, e_new = "quad", "2D4N", "2D3N"
        c_old, c_new = "3D4N", "3D3N"
    elif mode == "hex_to_tet":
        src_kind, e_old, e_new = "hex", "3D8N", "3D4N"
        c_old, c_new = "3D4N", "3D3N"
    else:
        raise ValueError("mode must be quad_to_tri, hex_to_tet or quadratic_to_linear")
    next_e = _next_id(mesh.element_ids())
    next_c = _next_id(mesh.condition_ids())
    split: dict[int, list[int]] = {}
    tet_faces: set[frozenset[int]] = set()
    converted = {"elements": 0, "conditions": 0}
    new_elements: dict[str, dict[int, tuple[int, list[int]]]] = {}
    for type_name in list(mesh.elements):
        by_id = mesh.elements[type_name]
        higher = sorted(e for e, (_, c) in by_id.items()
                        if cell_kind(mesh, type_name, c) == src_kind
                        and is_higher_order(mesh, type_name, c))
        if higher:
            raise ValueError(f"mode '{mode}' needs linear cells but element(s) {higher[:5]} "
                             f"({type_name}) are higher-order; run mode='quadratic_to_linear' first")
        targets = [e for e, (_, c) in by_id.items()
                   if cell_kind(mesh, type_name, c) == src_kind]
        if not targets:
            continue
        new_type = element_name or _rename(type_name, e_old, e_new)
        bucket = new_elements.setdefault(new_type, {})
        for eid in sorted(targets):
            pid, conn = by_id.pop(eid)
            if src_kind == "quad":
                pieces = [[conn[i] for i in tri] for tri in _QUAD_TO_TRI]
            else:
                pieces = []
                for t in _TETS_OF["hex"]:
                    cell = [conn[i] for i in t]
                    if _signed_tet(np.array([mesh.nodes[i] for i in cell])) < 0:
                        cell[1], cell[2] = cell[2], cell[1]
                    pieces.append(cell)
            ids = [eid]
            bucket[eid] = (pid, pieces[0])
            for piece in pieces[1:]:
                bucket[next_e] = (pid, piece)
                ids.append(next_e)
                next_e += 1
            split[eid] = ids
            converted["elements"] += 1
            if src_kind == "hex":
                for piece in pieces:
                    for f in FACES["tet"]:
                        tet_faces.add(frozenset(piece[i] for i in f))
        if not by_id:
            del mesh.elements[type_name]
    for new_type, bucket in new_elements.items():
        mesh.elements.setdefault(new_type, {}).update(bucket)

    cond_split: dict[int, list[int]] = {}
    for type_name in list(mesh.conditions):
        by_id = mesh.conditions[type_name]
        # Only 3D surface quads are split (2-node conditions of a 2D mesh are unchanged).
        targets = [c for c, (_, conn) in by_id.items()
                   if src_kind == "hex" and len(conn) == 4
                   and cell_kind(mesh, type_name, conn) == "quad"]
        if not targets:
            continue
        new_type = condition_name or _rename(type_name, c_old, c_new)
        bucket = mesh.conditions.setdefault(new_type, {})
        for cid in sorted(targets):
            pid, conn = by_id.pop(cid)
            a, b, c, d = conn
            diag_ac = frozenset((a, b, c)) in tet_faces and frozenset((a, c, d)) in tet_faces
            pieces = [[a, b, c], [a, c, d]] if diag_ac else [[a, b, d], [b, c, d]]
            bucket[cid] = (pid, pieces[0])
            ids = [cid]
            bucket[next_c] = (pid, pieces[1])
            ids.append(next_c)
            next_c += 1
            cond_split[cid] = ids
            converted["conditions"] += 1
        if not by_id:
            del mesh.conditions[type_name]
    for part in _walk(mesh):
        part.elements = _dedupe(n for e in part.elements for n in split.get(e, [e]))
        part.conditions = _dedupe(n for c in part.conditions for n in cond_split.get(c, [c]))
    return converted


def extract_boundary(mesh: Mdpa, condition_name: str | dict[str, str], part: str,
                     element_part: str | None = None, node_selector: dict[str, Any] | None = None,
                     property_id: int = 1) -> dict[str, Any]:
    """Create conditions on the free (non-shared) faces of the elements and
    put them in the SubModelPart ``part`` (created if missing, extended otherwise).

    ``element_part`` restricts to the elements of a part; ``node_selector`` (see
    select_nodes) keeps only faces whose every node is selected, e.g. a plane.
    ``condition_name`` is one name, or {"2": ..., "3": ..., ...} keyed by the
    face node count (needed for prisms, pyramids, mixed meshes and higher-order
    cells: quadratic triangle/quad edges have 3 nodes, cubic tri10/quad16 edges 4,
    tet10 and wedge/pyramid tri faces 6, hex20 and wedge/pyramid quad faces 8,
    hex27 faces 9, pyramid14 base 9). Faces are oriented
    outward for positively oriented elements; higher-order faces carry their
    mid-edge (and face) nodes in Kratos order."""
    if element_part:
        wanted = set(_require_part(mesh, element_part).elements)
    else:
        wanted = None
    counts: dict[frozenset[int], int] = {}
    candidates: list[tuple[tuple[int, ...], frozenset[int]]] = []
    for type_name, by_id in mesh.elements.items():
        for eid, (_, conn) in by_id.items():
            kind = cell_kind(mesh, type_name, conn)
            if kind not in FACES:
                continue
            n = len(conn)
            for face_index, local in enumerate(FACES[kind]):
                full = full_face_nodes(kind, n, local, face_index)
                face = tuple(conn[i] for i in full)
                key = frozenset(conn[i] for i in local)
                counts[key] = counts.get(key, 0) + 1
                if wanted is None or eid in wanted:
                    candidates.append((face, key))
    selected = set(select_nodes(mesh, node_selector)) if node_selector else None
    names = condition_name if isinstance(condition_name, dict) else None
    taken = mesh.condition_ids()
    next_id = _next_id(taken)
    new_ids: list[int] = []
    seen: set[frozenset[int]] = set()
    for face, key in candidates:
        if counts[key] != 1 or key in seen:
            continue
        if selected is not None and not all(n in selected for n in face):
            continue
        seen.add(key)
        name = names.get(str(len(face))) if names is not None else condition_name
        if not name:
            raise ValueError(f"No condition name given for faces with {len(face)} nodes; "
                             "pass condition_name as {\"2\": ..., \"3\": ..., \"4\": ...}")
        mesh.conditions.setdefault(name, {})[next_id] = (property_id, list(face))
        new_ids.append(next_id)
        next_id += 1
    if mdpa_mod.find_part(mesh, part) is None:
        create_part(mesh, part)
    add_members(mesh, part, conditions=new_ids)
    return {"conditions_created": len(new_ids), "condition_ids": [new_ids[0], new_ids[-1]]
            if new_ids else [], "part": part}


# --------------------------------------------------------------------- extrude

_EXTRUDE_ELEMENT = {"tri": "prism", "quad": "hex", "line": "quad"}


def _extruded_name(name: str, overrides: dict[str, str], n_old: int, n_new: int) -> str:
    if name in overrides:
        return overrides[name]
    return name.replace(f"2D{n_old}N", f"3D{n_new}N")


def extrude(mesh: Mdpa, heights: list[float], z0: float = 0.0,
            element_names: dict[str, str] | None = None,
            condition_names: dict[str, str] | None = None,
            cap_condition_names: dict[str, str] | None = None) -> Mdpa:
    """Extrude a planar (xy) mesh along z into a new 3D mesh.

    ``heights`` are the layer thicknesses (e.g. [0.1]*10). Triangles become
    prisms, quads hexahedra, 2-node conditions quads (3D4N); quadratic cells
    extrude order-preservingly (tri6 -> 15-node wedge, quad8 -> 20-node hex,
    3-node line conditions -> 8-node quads; quad9 needs ``quadratic_to_linear``
    first). Type names map by ``2DnN`` -> ``3DmN`` unless overridden in element_names / condition_names
    (for example ``{"LineLoadCondition2D2N": "SurfaceLoadCondition3D4N"}``).
    Every sub model part is carried over with all layers (with its data, table
    and property references); two new parts
    ``zmin`` and ``zmax`` hold the cap nodes (and cap conditions if
    cap_condition_names maps the face node count "3"/"4" ("6"/"8" for quadratic
    caps) to a condition type). Tables are carried over; ElementalData and
    ConditionalData rows are replicated for every layer; scalar NodalData is
    interpolated onto the new nodes (vertical midpoints average their neighbours,
    unparsable vector rows are dropped)."""
    if not heights or any(h <= 0 for h in heights):
        raise ValueError("heights needs one or more positive layer thicknesses")
    if mesh.geometries:
        raise ValueError("extrude does not support meshes with geometries")
    zs = {round(z, 12) for _, _, z in mesh.nodes.values()}
    if len(zs) != 1:
        raise ValueError("extrude needs a planar mesh (all nodes at the same z)")
    layers = len(heights)
    levels = [z0]
    for h in heights:
        levels.append(levels[-1] + h)
    n_stride = max(mesh.nodes, default=0)
    e_stride = max(mesh.element_ids(), default=0)
    c_stride = max(mesh.condition_ids(), default=0)
    element_names = element_names or {}
    condition_names = condition_names or {}
    out = Mdpa(model_part_data=dict(mesh.model_part_data),
               properties={k: dict(v) for k, v in mesh.properties.items()},
               tables={tid: mdpa_mod.Table(tid, t.x_variable, t.y_variable, list(t.rows))
                       for tid, t in mesh.tables.items()})

    def nid(n: int, layer: int) -> int:
        return n + layer * n_stride

    for layer, z in enumerate(levels):
        for n, (x, y, _) in mesh.nodes.items():
            out.nodes[nid(n, layer)] = (x, y, z)

    # Corner nodes of higher-order entities (vertical edges, hence new midpoint
    # nodes, run corner to corner). Linear entities extrude exactly as before.
    ho_corners: set[int] = set()
    for store in (mesh.elements, mesh.conditions):
        for type_name, by_id in store.items():
            for _, entry in by_id.items():
                conn = _conn(entry)
                kind = cell_kind(mesh, type_name, conn)
                idx = _CORNERS.get((kind or "", len(conn)))
                if idx is not None and len(idx) != len(conn):
                    ho_corners.update(conn[i] for i in idx)
    vbase = (layers + 1) * n_stride
    order = {n: k for k, n in enumerate(sorted(ho_corners))}
    vmid: dict[tuple[int, int], int] = {}
    for n in ho_corners:
        x, y, _ = mesh.nodes[n]
        for gap in range(layers):
            mid_id = vbase + order[n] * layers + gap + 1
            vmid[(n, gap)] = mid_id
            out.nodes[mid_id] = (x, y, (levels[gap] + levels[gap + 1]) / 2)

    _EXTRUDE_COUNTS = {("tri", 3): 6, ("tri", 6): 15, ("quad", 4): 8, ("quad", 8): 20,
                       ("line", 2): 4, ("line", 3): 8}

    for type_name, by_id in mesh.elements.items():
        for eid, (pid, conn) in by_id.items():
            kind = cell_kind(mesh, type_name, conn)
            if kind not in ("tri", "quad") or (kind, len(conn)) not in _EXTRUDE_COUNTS:
                raise ValueError(f"Element {eid} ({type_name}) cannot be extruded; "
                                 "only linear and quadratic (tri6/quad8) triangles and "
                                 "quadrilaterals (quadratic_to_linear first for the rest, "
                                 "e.g. cubic tri10/quad16)")
            n_new = _EXTRUDE_COUNTS[(kind, len(conn))]
            new_type = _extruded_name(type_name, element_names, len(conn), n_new)
            bucket = out.elements.setdefault(new_type, {})
            bottom_corners = corner_nodes(mesh, type_name, conn)
            quadratic = len(conn) != len(bottom_corners)
            cpos = list(_CORNERS.get((kind, len(conn)), range(len(conn))))
            mpos = [p for p in range(len(conn)) if p not in cpos]
            for layer in range(layers):
                if not quadratic:
                    bottom = [nid(n, layer) for n in conn]
                    top = [nid(n, layer + 1) for n in conn]
                    conn3d = bottom + top
                else:
                    # Kratos slot order: bottom corners, top corners, bottom
                    # mids, vertical mids, top mids.
                    b_c = [nid(conn[p], layer) for p in cpos]
                    t_c = [nid(conn[p], layer + 1) for p in cpos]
                    b_m = [nid(conn[p], layer) for p in mpos]
                    t_m = [nid(conn[p], layer + 1) for p in mpos]
                    vert = [vmid[(conn[p], layer)] for p in cpos]
                    conn3d = b_c + t_c + b_m + vert + t_m
                bucket[eid + layer * e_stride] = (pid, conn3d)
                for var, by_id_data in mesh.elemental_data.items():
                    if eid in by_id_data:
                        out.elemental_data.setdefault(var, {})[eid + layer * e_stride] = \
                            by_id_data[eid]
    for type_name, by_id in mesh.conditions.items():
        for cid, (pid, conn) in by_id.items():
            kind = cell_kind(mesh, type_name, conn)
            if kind != "line" or (kind, len(conn)) not in _EXTRUDE_COUNTS:
                raise ValueError(f"Condition {cid} ({type_name}) cannot be extruded; only "
                                 "2-node and 3-node line conditions")
            n_new = _EXTRUDE_COUNTS[(kind, len(conn))]
            new_type = _extruded_name(type_name, condition_names, len(conn), n_new)
            bucket = out.conditions.setdefault(new_type, {})
            corners_c = corner_nodes(mesh, type_name, conn)
            if len(corners_c) != 2:
                raise ValueError(f"Condition {cid} ({type_name}) cannot be extruded")
            a, b = corners_c
            mid = [n for n in conn if n not in corners_c]
            quadratic_c = len(conn) != 2
            for layer in range(layers):
                a_lo, b_lo = nid(a, layer), nid(b, layer)
                a_hi, b_hi = nid(a, layer + 1), nid(b, layer + 1)
                if not quadratic_c:
                    face = [a_lo, b_lo, b_hi, a_hi]
                else:
                    # Kratos quad8 slots: bottom mid, right vertical, top mid,
                    # left vertical.
                    m_lo, m_hi = nid(mid[0], layer), nid(mid[0], layer + 1)
                    face = [a_lo, b_lo, b_hi, a_hi,
                            m_lo, vmid[(b, layer)], m_hi, vmid[(a, layer)]]
                bucket[cid + layer * c_stride] = (pid, face)
                for var, by_id_data in mesh.conditional_data.items():
                    if cid in by_id_data:
                        out.conditional_data.setdefault(var, {})[cid + layer * c_stride] = \
                            by_id_data[cid]

    _carry_nodal_data(mesh, out, nid, vmid, layers)

    def copy(src: SubModelPart) -> SubModelPart:
        layer_nodes = [nid(n, k) for n in src.nodes for k in range(layers + 1)]
        layer_nodes += [vmid[(n, g)] for n in src.nodes for g in range(layers)
                        if (n, g) in vmid]
        dst = SubModelPart(
            src.name,
            nodes=layer_nodes,
            elements=[e + k * e_stride for e in src.elements for k in range(layers)],
            conditions=[c + k * c_stride for c in src.conditions for k in range(layers)],
            data=dict(src.data), tables=list(src.tables), properties=list(src.properties))
        dst.children = {n: copy(c) for n, c in src.children.items()}
        return dst

    for part in mesh.sub_model_parts.values():
        out.sub_model_parts[part.name] = copy(part)
    caps = {"zmin": 0, "zmax": layers}
    cap_names = cap_condition_names or {}
    next_c = _next_id(out.condition_ids())
    for cap, layer in caps.items():
        if cap in out.sub_model_parts:
            raise ValueError(f"The mesh already has a part called '{cap}'; rename it first")
        part = SubModelPart(cap, nodes=[nid(n, layer) for n in sorted(mesh.nodes)])
        gap = 0 if layer == 0 else layers - 1
        part.nodes += [vid for (_, g), vid in sorted(vmid.items()) if g == gap]
        for type_name, by_id in mesh.elements.items():
            for eid, (pid, conn) in sorted(by_id.items()):
                kind = cell_kind(mesh, type_name, conn)
                idx = _CORNERS.get((kind or "", len(conn)), tuple(range(len(conn))))
                loop = [idx[0]] + list(reversed(idx[1:])) if cap == "zmin" else list(idx)
                full = [nid(conn[p], layer) for p in loop]
                mids = _EDGE_MIDS.get((kind or "", len(conn)), {})
                extended = list(loop) + [loop[0]]
                for a, b in zip(extended, extended[1:]):
                    slot = mids.get(frozenset((a, b)))
                    if slot is not None:
                        full.append(nid(conn[slot], layer))
                name = cap_names.get(str(len(full)))
                if not name:
                    continue
                out.conditions.setdefault(name, {})[next_c] = (pid, full)
                part.conditions.append(next_c)
                next_c += 1
        out.sub_model_parts[cap] = part
    return out


def _carry_nodal_data(mesh: Mdpa, out: Mdpa, nid: Callable[[int, int], int],
                      vmid: dict[tuple[int, int], int], layers: int) -> None:
    """Replicate scalar NodalData onto every extrusion layer; vertical midpoints
    average their neighbours. Unparsable vector rows are dropped (only for the
    new midpoint nodes; layer copies are verbatim)."""

    for var, values in mesh.nodal_data.items():
        carried = out.nodal_data.setdefault(var, {})
        for n, v in values.items():
            for k in range(layers + 1):
                carried[nid(n, k)] = v
        for (n, gap), mid_id in vmid.items():
            lo, hi = nid(n, gap), nid(n, gap + 1)
            if lo in carried and hi in carried:
                carried[mid_id] = (carried[lo] + carried[hi]) / 2
        fixed = mesh.nodal_data_fixed.get(var)
        if fixed:
            out_fixed = out.nodal_data_fixed.setdefault(var, set())
            for n in fixed:
                for k in range(layers + 1):
                    out_fixed.add(nid(n, k))
            for (n, gap), mid_id in vmid.items():
                if nid(n, gap) in out_fixed or nid(n, gap + 1) in out_fixed:
                    out_fixed.add(mid_id)
    for var, values in mesh.vector_nodal_data.items():
        carried = out.vector_nodal_data.setdefault(var, {})
        parsed: dict[int, tuple[bool, list[float]]] = {}
        for n, (is_fixed, raw) in values.items():
            for k in range(layers + 1):
                carried[nid(n, k)] = (is_fixed, raw)
            m = re.fullmatch(r"\[\d+\]\((.*)\)", raw.strip())
            if m:
                try:
                    parsed[n] = (is_fixed, [float(x) for x in m.group(1).split(",")])
                except ValueError:
                    pass
        for (n, gap), mid_id in vmid.items():
            if n in parsed and nid(n, gap) in carried and nid(n, gap + 1) in carried:
                is_fixed, nums = parsed[n]
                avg = ", ".join(repr((a + b) / 2) for a, b in zip(nums, nums))
                carried[mid_id] = (is_fixed, f"[{len(nums)}]({avg})")
    for var, ids in mesh.nodal_flags.items():
        out.nodal_flags.setdefault(var, []).extend(nid(n, k) for n in ids for k in range(layers + 1))
