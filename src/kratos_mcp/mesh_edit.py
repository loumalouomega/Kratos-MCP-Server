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
from typing import Any, Iterable

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
    connectivity of linear elements/conditions so they keep a positive measure."""
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


def _flip_connectivity(mesh: Mdpa, type_name: str, conn: list[int]) -> list[int]:
    kind = cell_kind(mesh, type_name, conn)
    c = list(conn)
    if kind == "tri":
        return [c[0], c[2], c[1]]
    if kind == "tet":
        return [c[0], c[2], c[1], c[3]]
    if kind == "quad":
        return [c[0], c[3], c[2], c[1]]
    if kind == "hex":
        return [c[0], c[3], c[2], c[1], c[4], c[7], c[6], c[5]]
    if kind == "prism":
        return [c[0], c[2], c[1], c[3], c[5], c[4]]
    return c


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
}
FACES = {
    "tri": [(0, 1), (1, 2), (2, 0)],
    "quad": [(0, 1), (1, 2), (2, 3), (3, 0)],
    "tet": [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)],
    "hex": [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    "prism": [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
}
_TETS_OF = {
    "tet": [(0, 1, 2, 3)],
    "prism": [(0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5)],
    "hex": [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)],
}


def cell_kind(mesh: Mdpa, type_name: str, conn: list[int]) -> str | None:
    """Linear cell kind of an entity: line, tri, quad, tet, prism, hex (None if
    it is not one of them). A 4-node entity is a tet when its nodes span a
    volume and a quad when they are planar."""
    n = len(conn)
    if n == 2:
        return "line"
    if n == 3:
        return "tri"
    if n == 6:
        return "prism"
    if n == 8:
        return "hex"
    if n == 4:
        p = np.array([mesh.nodes[i] for i in conn])
        scale = max(float(np.ptp(p, axis=0).max()), 1e-300)
        vol = abs(float(np.linalg.det(p[1:] - p[0]))) / scale ** 3
        return "tet" if vol > 1e-9 else "quad"
    return None


def _signed_tet(p: np.ndarray) -> float:
    return float(np.linalg.det(p[1:] - p[0])) / 6.0


def measure(mesh: Mdpa, type_name: str, conn: list[int]) -> float | None:
    """Length / area / volume of a linear cell; negative when inverted (areas
    are signed only for cells lying in a constant-z plane)."""
    kind = cell_kind(mesh, type_name, conn)
    if kind is None:
        return None
    p = np.array([mesh.nodes[i] for i in conn])
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
    return [float(np.linalg.norm(np.array(mesh.nodes[conn[a]]) - np.array(mesh.nodes[conn[b]])))
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
    for part in _walk(mesh):
        part.nodes = _dedupe(replace.get(n, n) for n in part.nodes)
    return {"merged": len(replace), "degenerate_entities": degenerate}


def _remap_everywhere(mesh: Mdpa, node_map: dict[int, int], ent_maps: dict[str, dict[int, int]]
                      ) -> None:
    mesh.nodes = {node_map[n]: xyz for n, xyz in mesh.nodes.items()}
    mesh.nodal_data = {var: {node_map[n]: v for n, v in values.items() if n in node_map}
                       for var, values in mesh.nodal_data.items()}
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
    pids = {entry[0] for store in (out.elements, out.conditions)
            for by_id in store.values() for entry in by_id.values()}
    out.properties = {pid: dict(mesh.properties[pid]) for pid in pids if pid in mesh.properties}

    def copy(src: SubModelPart) -> SubModelPart:
        dst = SubModelPart(src.name, nodes=list(src.nodes), elements=list(src.elements),
                           conditions=list(src.conditions), geometries=list(src.geometries))
        dst.children = {n: copy(c) for n, c in src.children.items()}
        return dst

    out.sub_model_parts[part.name] = copy(part)
    return out


def set_nodal_data(mesh: Mdpa, variable: str, node_ids: Iterable[int], value: float) -> int:
    ids = [int(n) for n in node_ids]
    missing = [n for n in ids if n not in mesh.nodes]
    if missing:
        raise ValueError(f"Missing nodes {missing[:10]}")
    mesh.nodal_data.setdefault(variable, {}).update({n: float(value) for n in ids})
    return len(ids)


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


def convert_cells(mesh: Mdpa, mode: str, element_name: str | None = None,
                  condition_name: str | None = None) -> dict[str, int]:
    """Split quadrilaterals into 2 triangles (``quad_to_tri``) or hexahedra into
    6 tetrahedra (``hex_to_tet``, Kuhn split: conforming on structured meshes
    with a consistent node ordering; boundary quads follow the tet faces).

    Entities of the other cell kinds are untouched. New type names default to the
    old ones with ``2D4N``->``2D3N`` / ``3D8N``->``3D4N`` (``3D4N`` -> ``3D3N``
    for conditions); pass element_name / condition_name to override."""
    if mode == "quad_to_tri":
        src_kind, e_old, e_new = "quad", "2D4N", "2D3N"
        c_old, c_new = "3D4N", "3D3N"
    elif mode == "hex_to_tet":
        src_kind, e_old, e_new = "hex", "3D8N", "3D4N"
        c_old, c_new = "3D4N", "3D3N"
    else:
        raise ValueError("mode must be quad_to_tri or hex_to_tet")
    next_e = _next_id(mesh.element_ids())
    next_c = _next_id(mesh.condition_ids())
    split: dict[int, list[int]] = {}
    tet_faces: set[frozenset[int]] = set()
    converted = {"elements": 0, "conditions": 0}
    new_elements: dict[str, dict[int, tuple[int, list[int]]]] = {}
    for type_name in list(mesh.elements):
        by_id = mesh.elements[type_name]
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
    """Create conditions on the free (non-shared) faces of linear elements and
    put them in the SubModelPart ``part`` (created if missing, extended otherwise).

    ``element_part`` restricts to the elements of a part; ``node_selector`` (see
    select_nodes) keeps only faces whose every node is selected, e.g. a plane.
    ``condition_name`` is one name, or {"2": ..., "3": ..., "4": ...} keyed by the
    face node count (needed for prisms or mixed meshes). Faces are oriented
    outward for positively oriented elements."""
    if element_part:
        wanted = set(_require_part(mesh, element_part).elements)
    else:
        wanted = None
    counts: dict[frozenset[int], int] = {}
    candidates: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    for type_name, by_id in mesh.elements.items():
        for eid, (_, conn) in by_id.items():
            kind = cell_kind(mesh, type_name, conn)
            if kind not in FACES:
                continue
            for local in FACES[kind]:
                face = tuple(conn[i] for i in local)
                key = frozenset(face)
                counts[key] = counts.get(key, 0) + 1
                if wanted is None or eid in wanted:
                    candidates.append((face, tuple(sorted(face))))
    selected = set(select_nodes(mesh, node_selector)) if node_selector else None
    names = condition_name if isinstance(condition_name, dict) else None
    taken = mesh.condition_ids()
    next_id = _next_id(taken)
    new_ids: list[int] = []
    seen: set[frozenset[int]] = set()
    for face, _ in candidates:
        key = frozenset(face)
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
    prisms, quads hexahedra, 2-node conditions quads (3D4N). Type names map by
    ``2DnN`` -> ``3DmN`` unless overridden in element_names / condition_names
    (for example ``{"LineLoadCondition2D2N": "SurfaceLoadCondition3D4N"}``).
    Every sub model part is carried over with all layers; two new parts
    ``zmin`` and ``zmax`` hold the cap nodes (and cap conditions if
    cap_condition_names maps the face node count "3"/"4" to a condition type)."""
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
               properties={k: dict(v) for k, v in mesh.properties.items()})

    def nid(n: int, layer: int) -> int:
        return n + layer * n_stride

    for layer, z in enumerate(levels):
        for n, (x, y, _) in mesh.nodes.items():
            out.nodes[nid(n, layer)] = (x, y, z)
    for type_name, by_id in mesh.elements.items():
        for eid, (pid, conn) in by_id.items():
            kind = cell_kind(mesh, type_name, conn)
            if kind not in ("tri", "quad"):
                raise ValueError(f"Element {eid} ({type_name}) cannot be extruded; "
                                 "only triangles and quadrilaterals")
            new_type = _extruded_name(type_name, element_names, len(conn),
                                      6 if kind == "tri" else 8)
            bucket = out.elements.setdefault(new_type, {})
            for layer in range(layers):
                bottom = [nid(n, layer) for n in conn]
                top = [nid(n, layer + 1) for n in conn]
                bucket[eid + layer * e_stride] = (pid, bottom + top)
    for type_name, by_id in mesh.conditions.items():
        for cid, (pid, conn) in by_id.items():
            if len(conn) != 2:
                raise ValueError(f"Condition {cid} ({type_name}) cannot be extruded; only "
                                 "2-node line conditions")
            new_type = _extruded_name(type_name, condition_names, 2, 4)
            bucket = out.conditions.setdefault(new_type, {})
            a, b = conn
            for layer in range(layers):
                bucket[cid + layer * c_stride] = (
                    pid, [nid(a, layer), nid(b, layer), nid(b, layer + 1), nid(a, layer + 1)])

    def copy(src: SubModelPart) -> SubModelPart:
        dst = SubModelPart(
            src.name,
            nodes=[nid(n, k) for n in src.nodes for k in range(layers + 1)],
            elements=[e + k * e_stride for e in src.elements for k in range(layers)],
            conditions=[c + k * c_stride for c in src.conditions for k in range(layers)])
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
        for type_name, by_id in mesh.elements.items():
            for eid, (pid, conn) in sorted(by_id.items()):
                name = cap_names.get(str(len(conn)))
                if not name:
                    continue
                face = [nid(n, layer) for n in conn]
                if cap == "zmin":
                    face = face[::-1]
                out.conditions.setdefault(name, {})[next_c] = (pid, face)
                part.conditions.append(next_c)
                next_c += 1
        out.sub_model_parts[cap] = part
    return out
