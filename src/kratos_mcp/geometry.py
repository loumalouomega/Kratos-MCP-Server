"""Kratos-free parametric geometry generators beyond the structured box.

All of them return an :class:`~kratos_mcp.mdpa.Mdpa` that follows the naming
convention of ``create_rectangle_mesh``: a ``domain`` part with every node and
element plus named boundary parts holding the boundary nodes and (when a
condition type is given) the boundary conditions. Arbitrary shapes with holes
or CAD input need the optional gmsh route (see ``mesh_io.gmsh_generate``); a
polygon can also be graded without gmsh (``size_field`` / ``size_sources``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import mesh_edit
from .mdpa import Mdpa, SubModelPart
from .remesh import compile_expression


def _attach_boundaries(m: Mdpa, boundaries: dict[str, list[list[int]]],
                       condition_name: str | None, property_id: int,
                       root_part: str) -> Mdpa:
    """Create ``domain`` + one part per boundary (list of faces/edges as node lists)."""
    elems = [e for by_id in m.elements.values() for e in by_id]
    m.sub_model_parts[root_part] = SubModelPart(root_part, nodes=sorted(m.nodes),
                                                elements=sorted(elems))
    cid = 1
    conds = m.conditions.setdefault(condition_name, {}) if condition_name else None
    for name, faces in boundaries.items():
        part = SubModelPart(name, nodes=sorted({n for f in faces for n in f}))
        if conds is not None:
            for face in faces:
                conds[cid] = (property_id, list(face))
                part.conditions.append(cid)
                cid += 1
        m.sub_model_parts[name] = part
    if conds is not None and not conds:
        del m.conditions[condition_name]
    return m


def create_annulus_mesh(r_inner: float, r_outer: float, n_radial: int, n_theta: int,
                        angle: float = 360.0, element_name: str | None = None,
                        condition_name: str | None = "LineLoadCondition2D2N",
                        triangles: bool = False, property_id: int = 1,
                        root_part: str = "domain") -> Mdpa:
    """Structured polar mesh of a ring (or a sector when angle < 360). Parts:
    ``inner``, ``outer`` and, for a sector, ``start`` / ``end`` (the straight sides)."""
    if not 0 < r_inner < r_outer:
        raise ValueError("need 0 < r_inner < r_outer (use a disc for a full circle)")
    if n_radial < 1 or n_theta < (3 if angle >= 360 else 1):
        raise ValueError("n_radial >= 1 and n_theta >= 3 (>= 1 for a sector) are required")
    full = angle >= 360.0
    element_name = element_name or (
        "SmallDisplacementElement2D3N" if triangles else "SmallDisplacementElement2D4N")
    n_cols = n_theta if full else n_theta + 1

    def nid(ir: int, it: int) -> int:
        return ir * n_cols + (it % n_cols) + 1

    m = Mdpa()
    for ir in range(n_radial + 1):
        r = r_inner + (r_outer - r_inner) * ir / n_radial
        for it in range(n_cols):
            a = math.radians(angle) * it / n_theta
            m.nodes[nid(ir, it)] = (r * math.cos(a), r * math.sin(a), 0.0)
    elems = m.elements.setdefault(element_name, {})
    eid = 1
    for ir in range(n_radial):
        for it in range(n_theta):
            a, b = nid(ir, it), nid(ir, it + 1)
            c, d = nid(ir + 1, it + 1), nid(ir + 1, it)
            # a -> b runs counter-clockwise and b -> c outward, which is clockwise
            # overall: list the corners as a, d, c, b to get a positive area.
            if triangles:
                elems[eid] = (property_id, [a, c, b]); eid += 1
                elems[eid] = (property_id, [a, d, c]); eid += 1
            else:
                elems[eid] = (property_id, [a, d, c, b]); eid += 1
    # Boundary edges run with the domain on their left, like create_rectangle_mesh.
    boundaries = {
        "inner": [[nid(0, it + 1), nid(0, it)] for it in range(n_theta)],
        "outer": [[nid(n_radial, it), nid(n_radial, it + 1)] for it in range(n_theta)],
    }
    if not full:
        boundaries["start"] = [[nid(ir, 0), nid(ir + 1, 0)] for ir in range(n_radial)]
        boundaries["end"] = [[nid(ir + 1, n_theta), nid(ir, n_theta)] for ir in range(n_radial)]
    return _attach_boundaries(m, boundaries, condition_name, property_id, root_part)


def create_disc_mesh(radius: float, n_radial: int, n_theta: int,
                     element_name: str = "SmallDisplacementElement2D3N",
                     condition_name: str | None = "LineLoadCondition2D2N",
                     property_id: int = 1, root_part: str = "domain") -> Mdpa:
    """Triangle mesh of a disc: a fan at the centre plus triangulated rings.
    Part ``boundary`` is the circle."""
    if radius <= 0 or n_radial < 1 or n_theta < 3:
        raise ValueError("radius > 0, n_radial >= 1 and n_theta >= 3 are required")
    m = Mdpa()
    m.nodes[1] = (0.0, 0.0, 0.0)

    def nid(ir: int, it: int) -> int:  # ir in 1..n_radial
        return 2 + (ir - 1) * n_theta + (it % n_theta)

    for ir in range(1, n_radial + 1):
        r = radius * ir / n_radial
        for it in range(n_theta):
            a = 2 * math.pi * it / n_theta
            m.nodes[nid(ir, it)] = (r * math.cos(a), r * math.sin(a), 0.0)
    elems = m.elements.setdefault(element_name, {})
    eid = 1
    for it in range(n_theta):
        elems[eid] = (property_id, [1, nid(1, it), nid(1, it + 1)]); eid += 1
    for ir in range(1, n_radial):
        for it in range(n_theta):
            a, b = nid(ir, it), nid(ir, it + 1)
            c, d = nid(ir + 1, it + 1), nid(ir + 1, it)
            elems[eid] = (property_id, [a, c, b]); eid += 1
            elems[eid] = (property_id, [a, d, c]); eid += 1
    boundaries = {"boundary": [[nid(n_radial, it), nid(n_radial, it + 1)]
                               for it in range(n_theta)]}
    return _attach_boundaries(m, boundaries, condition_name, property_id, root_part)


def create_cylinder_mesh(radius: float, height: float, n_radial: int, n_theta: int, n_z: int,
                         inner_radius: float | None = None,
                         element_name: str | None = None,
                         condition_name: str | None = "SurfaceLoadCondition3D4N",
                         property_id: int = 1, root_part: str = "domain") -> Mdpa:
    """Solid (prisms) or hollow (hexahedra, inner_radius given) cylinder along z.
    Parts: ``lateral`` (``inner``/``outer`` when hollow), ``zmin``, ``zmax``."""
    if height <= 0 or n_z < 1:
        raise ValueError("height > 0 and n_z >= 1 are required")
    if inner_radius:
        base = create_annulus_mesh(inner_radius, radius, n_radial, n_theta,
                                   element_name="SmallDisplacementElement2D4N",
                                   condition_name="LineCondition2D2N", property_id=property_id)
        names = {"SmallDisplacementElement2D4N": element_name or "SmallDisplacementElement3D8N"}
    else:
        base = create_disc_mesh(radius, n_radial, n_theta,
                                element_name="SmallDisplacementElement2D3N",
                                condition_name="LineCondition2D2N", property_id=property_id)
        names = {"SmallDisplacementElement2D3N": element_name or "SmallDisplacementElement3D6N"}
    caps = ({"3": condition_name.replace("3D4N", "3D3N"), "4": condition_name}
            if condition_name else None)
    out = mesh_edit.extrude(
        base, [height / n_z] * n_z, element_names=names,
        condition_names={"LineCondition2D2N": condition_name or "SurfaceCondition3D4N"},
        cap_condition_names=caps)
    if not condition_name:
        out.conditions.clear()
        for part in mesh_edit._walk(out):
            part.conditions.clear()
    # tidy: boundary of the base mesh becomes the lateral wall
    if "boundary" in out.sub_model_parts:
        out.sub_model_parts["lateral"] = out.sub_model_parts.pop("boundary")
    return out


def create_sphere_mesh(radius: float, divisions: int, element_name: str = "SmallDisplacementElement3D8N",
                       condition_name: str | None = "SurfaceLoadCondition3D4N",
                       property_id: int = 1, root_part: str = "domain") -> Mdpa:
    """Solid ball of hexahedra: a (divisions)^3 box radially mapped onto the
    sphere (valid but with stretched corner cells). Part ``surface``."""
    from .mdpa import create_box_mesh

    if radius <= 0 or divisions < 2:
        raise ValueError("radius > 0 and divisions >= 2 are required")
    m = create_box_mesh(2.0, 2.0, 2.0, divisions, divisions, divisions,
                        element_name=element_name, condition_name=condition_name or "X",
                        property_id=property_id, root_part=root_part)
    for nid, (x, y, z) in list(m.nodes.items()):
        p = np.array([x - 1.0, y - 1.0, z - 1.0])
        sup, eucl = float(np.abs(p).max()), float(np.linalg.norm(p))
        scale = radius * sup / eucl if eucl > 0 else 0.0
        m.nodes[nid] = tuple(float(v) for v in p * scale)
    faces = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]
    surface = SubModelPart("surface")
    for name in faces:
        part = m.sub_model_parts.pop(name)
        surface.nodes += part.nodes
        surface.conditions += part.conditions
    surface.nodes = sorted(set(surface.nodes))
    m.sub_model_parts["surface"] = surface
    if not condition_name:
        m.conditions.clear()
        surface.conditions.clear()
    return m


# ------------------------------------------------------------------- polygons

def _signed_area(poly: list[tuple[float, float]]) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))


def _point_in_tri(p, a, b, c) -> bool:
    def side(u, v, w):
        return (u[0] - w[0]) * (v[1] - w[1]) - (v[0] - w[0]) * (u[1] - w[1])

    d1, d2, d3 = side(p, a, b), side(p, b, c), side(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _check_simple(poly: list[tuple[float, float]]) -> None:
    n = len(poly)

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                raise ValueError("the polygon is self-intersecting")


def _point_in_polygon(p: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """Ray casting (even-odd); points on the boundary count as outside."""
    inside = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > p[1]) != (y1 > p[1]) and \
                p[0] < (x1 - x0) * (p[1] - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _segments_cross(a, b, c, d) -> bool:
    """True when the open segments (a, b) and (c, d) properly intersect."""

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def _bridge_holes(outer: list[tuple[float, float]],
                  holes: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Merge holes into one (weakly simple) polygon by zero-width slits: each
    hole is spliced into the loop as ``..., p, h, <hole loop back to h>, p,
    ...`` where (p, h) is the shortest visible outer-hole vertex pair. The
    slit is traversed out and back along the same segment, so it needs no
    triangulation; the outer loop runs counter-clockwise and each hole
    clockwise around its interior, keeping hole interiors outside."""
    merged = list(outer)
    if _signed_area(merged) < 0:
        merged.reverse()
    oriented = []
    for hole in holes:
        h = list(hole)
        if _signed_area(h) > 0:
            h.reverse()
        oriented.append(h)

    def loop_edges(loop: list[tuple[float, float]]):
        return [(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]

    for k, hole in enumerate(oriented):
        # Edges the slit must not cross: the current loop plus later holes.
        blockers = loop_edges(merged) + [e for h in oriented[k + 1:] for e in loop_edges(h)]
        hole_edges = loop_edges(hole)
        best: tuple[float, int, int] | None = None
        for i, p in enumerate(merged):
            for j, q in enumerate(hole):
                dist = math.dist(p, q)
                if best is not None and dist >= best[0]:
                    continue
                if any(_segments_cross(p, q, a, b) for a, b in blockers + hole_edges):
                    continue
                mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                if not _point_in_polygon(mid, outer):
                    continue
                if any(_point_in_polygon(mid, h) for h in oriented):
                    continue
                best = (dist, i, j)
        if best is None:
            raise ValueError(f"hole {k} cannot be bridged to the polygon "
                             "(it may touch the boundary or another hole)")
        _, i, j = best
        order = [(j + t) % len(hole) for t in range(len(hole) + 1)]
        merged = merged[:i + 1] + [hole[t] for t in order] + merged[i:]
    return merged
    n = len(poly)

    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                raise ValueError("the polygon is self-intersecting")


def ear_clip(poly: list[tuple[float, float]],
             walls: list[tuple[tuple[float, float], tuple[float, float]]] | None = None
             ) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon (counter-clockwise or clockwise; holes must
    be bridged into the loop first, see ``_bridge_holes``); returns index
    triples, counter-clockwise. ``walls`` are segments an ear diagonal must not
    cross: for a plain loop they default to its own edges (always satisfied),
    for a bridged loop pass the outer plus hole edges, since clipped hole
    vertices no longer block invalid diagonals."""
    n = len(poly)
    if n < 3:
        raise ValueError("a polygon needs at least 3 vertices")
    idx = list(range(n))
    if abs(_signed_area(poly)) < 1e-14:
        raise ValueError("the polygon has zero area (or is self-intersecting)")
    _check_simple(poly)
    if walls is None:
        walls = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    if _signed_area(poly) < 0:
        idx.reverse()
    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > n * n:
            raise ValueError("could not triangulate: the polygon is self-intersecting or degenerate")
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-14:
                continue  # reflex or collinear
            if any(_point_in_tri(poly[j], a, b, c) for j in idx
                   if j not in (i0, i1, i2) and poly[j] not in (a, b, c)):
                continue
            if any(_segments_cross(a, c, w0, w1) for w0, w1 in walls):
                continue  # diagonal across a hole or outside the polygon
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            raise ValueError("could not triangulate: the polygon is self-intersecting or degenerate")
    tris.append((idx[0], idx[1], idx[2]))
    return tris


def size_function(size_field: str | None = None,
                  size_sources: list[dict[str, Any]] | None = None):
    """``h(x, y)`` target edge length from an expression of ``x``/``y`` (see
    ``remesh.compile_expression``) and/or sources ``{"point": [x, y]}`` or
    ``{"segment": [[x0, y0], [x1, y1]]}`` with ``size`` (at the source) and
    ``growth`` (length gained per unit distance, default 0.3); the result is the
    minimum over everything given. Raises ValueError."""
    if size_field is None and not size_sources:
        raise ValueError("give size_field and/or size_sources")
    fields = [compile_expression(size_field)] if size_field is not None else []
    sources: list[tuple[np.ndarray, np.ndarray, float, float]] = []
    for k, src in enumerate(size_sources or []):
        if not isinstance(src, dict) or ("point" in src) == ("segment" in src):
            raise ValueError(f"size_sources[{k}] needs exactly one of 'point' or 'segment'")
        try:
            pts = [src["point"]] * 2 if "point" in src else list(src["segment"])
            a, b = (np.array([float(v[0]), float(v[1])]) for v in pts)
            size = float(src.get("size"))
            growth = float(src.get("growth", 0.3))
        except (TypeError, ValueError, IndexError, KeyError):
            raise ValueError(f"size_sources[{k}] is malformed: point [x, y] or segment "
                             "[[x0, y0], [x1, y1]] plus a numeric size (and growth)") from None
        if not (math.isfinite(size) and size > 0 and math.isfinite(growth) and growth >= 0):
            raise ValueError(f"size_sources[{k}]: size must be positive and growth >= 0")
        sources.append((a, b, size, growth))

    def h(x: float, y: float) -> float:
        values: list[float] = []
        for field in fields:
            try:
                values.append(float(field(x, y, 0.0)))
            except (ArithmeticError, ValueError) as exc:
                raise ValueError(f"size_field failed at ({x:.6g}, {y:.6g}): {exc}") from None
        p = np.array([x, y])
        for a, b, size, growth in sources:
            d = b - a
            denom = float(d @ d)
            t = 0.0 if denom == 0 else min(1.0, max(0.0, float((p - a) @ d) / denom))
            values.append(size + growth * float(np.linalg.norm(p - (a + t * d))))
        out = min(values)
        if not (math.isfinite(out) and out > 0):
            raise ValueError(f"the size field is not positive and finite at ({x:.6g}, {y:.6g})")
        return out

    return h


def grade_triangles(points: list[tuple[float, float]], tris: list[tuple[int, int, int]],
                    h, max_elements: int = 200000
                    ) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """Conforming Rivara longest-edge refinement: every triangle whose longest edge
    exceeds ``h`` (the smallest of ``h`` at its corners and centroid) is bisected;
    the neighbour across the split edge is bisected with it, after first refining
    its own longest edge when that is a different one, so the mesh never gets
    hanging nodes. ``tris`` must be counter-clockwise; boundary edges stay on
    their original segments. Raises ValueError past ``max_elements``."""
    points = list(points)
    alive: dict[int, tuple[int, int, int]] = dict(enumerate(tris))
    edge_tris: dict[tuple[int, int], set[int]] = {}
    next_id = len(alive)

    def key(u: int, v: int) -> tuple[int, int]:
        return (u, v) if u < v else (v, u)

    def link(t: int) -> None:
        a, b, c = alive[t]
        for u, v in ((a, b), (b, c), (c, a)):
            edge_tris.setdefault(key(u, v), set()).add(t)

    def unlink(t: int) -> None:
        a, b, c = alive[t]
        for u, v in ((a, b), (b, c), (c, a)):
            edge_tris[key(u, v)].discard(t)

    def order(u: int, v: int) -> tuple[float, int, int]:
        # strict total order on edges: equal lengths are tie-broken by node ids, so
        # two triangles sharing an edge always agree on which edge is longer
        return (round(math.dist(points[u], points[v]), 12), *key(u, v))

    def longest(t: int) -> tuple[int, int]:
        a, b, c = alive[t]
        return max(((a, b), (b, c), (c, a)), key=lambda e: order(*e))

    def split(t: int, edge: tuple[int, int], mid: int, created: list[int]) -> None:
        nonlocal next_id
        a, b, c = alive[t]
        for i, (u, v, w) in enumerate(((a, b, c), (b, c, a), (c, a, b))):
            if key(u, v) == key(*edge):
                break
        unlink(t)
        del alive[t]
        for child in ((u, mid, w), (mid, v, w)):
            alive[next_id] = child
            link(next_id)
            created.append(next_id)
            next_id += 1

    def bisect(t: int, created: list[int]) -> None:
        path = [t]
        while path:
            cur = path[-1]
            edge = longest(cur)
            others = edge_tris[key(*edge)] - {cur}
            nb = next(iter(others)) if others else None
            if nb is not None and key(*longest(nb)) != key(*edge):
                path.append(nb)
                continue
            u, v = edge
            points.append(((points[u][0] + points[v][0]) / 2, (points[u][1] + points[v][1]) / 2))
            mid = len(points) - 1
            split(cur, edge, mid, created)
            if nb is not None:
                split(nb, edge, mid, created)
                path = [p for p in path if p in alive]
            else:
                path.pop()
            path = [p for p in path if p in alive]
            if len(alive) > max_elements:
                raise ValueError(f"grading needs more than max_elements={max_elements} "
                                 "triangles; use a larger size or raise max_elements")

    def marked(t: int) -> bool:
        a, b, c = alive[t]
        pa, pb, pc = points[a], points[b], points[c]
        centroid = ((pa[0] + pb[0] + pc[0]) / 3, (pa[1] + pb[1] + pc[1]) / 3)
        target = min(h(*pa), h(*pb), h(*pc), h(*centroid))
        return math.dist(points[longest(t)[0]], points[longest(t)[1]]) > target * (1 + 1e-9)

    for t in list(alive):
        link(t)
    queue = list(alive)
    while queue:
        t = queue.pop()
        if t not in alive or not marked(t):
            continue
        created: list[int] = []
        bisect(t, created)
        queue += [c for c in created if c in alive]
    return points, list(alive.values())


def smooth_interior(points: list[tuple[float, float]], tris: list[tuple[int, int, int]],
                    iterations: int) -> list[tuple[float, float]]:
    """Laplacian smoothing of the nodes that do not lie on a free edge; a move that
    would flip or collapse an adjacent triangle is skipped."""
    points = list(points)
    counts: dict[tuple[int, int], int] = {}
    neighbours: dict[int, set[int]] = {}
    incident: dict[int, list[int]] = {}
    for t, (a, b, c) in enumerate(tris):
        for u, v in ((a, b), (b, c), (c, a)):
            k = (min(u, v), max(u, v))
            counts[k] = counts.get(k, 0) + 1
            neighbours.setdefault(u, set()).add(v)
            neighbours.setdefault(v, set()).add(u)
        for n in (a, b, c):
            incident.setdefault(n, []).append(t)
    fixed = {n for (u, v), c in counts.items() if c == 1 for n in (u, v)}

    def area(t: int) -> float:
        a, b, c = (points[i] for i in tris[t])
        return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])

    for _ in range(iterations):
        for n in sorted(neighbours):
            if n in fixed:
                continue
            old = points[n]
            ring = [points[m] for m in neighbours[n]]
            points[n] = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
            if any(area(t) <= 0 for t in incident[n]):
                points[n] = old
    return points


def create_polygon_mesh(vertices: list[list[float]], refinements: int = 0,
                        element_name: str = "SmallDisplacementElement2D3N",
                        condition_name: str | None = "LineLoadCondition2D2N",
                        property_id: int = 1, root_part: str = "domain",
                        holes: list[list[list[float]]] | None = None,
                        target_size: float | None = None,
                        size_field: str | None = None,
                        size_sources: list[dict[str, Any]] | None = None,
                        max_elements: int = 200000,
                        smooth_iterations: int = 0) -> Mdpa:
    """Triangle mesh of a simple polygon, optionally with holes: ear clipping,
    then ``refinements`` rounds of midpoint subdivision (each splits a triangle
    in 4). One boundary part ``side_<i>`` per outer edge (edge i joins vertex i
    and i+1) plus ``hole_<k>`` per hole loop. ``target_size`` adds uniform
    subdivision rounds until no edge is longer than it (at most 6 rounds total;
    best effort). ``size_field`` (expression of x, y) and/or ``size_sources``
    (point / segment sources with ``size`` and ``growth``, see
    :func:`size_function`) then grade the mesh by conforming longest-edge
    bisection until each triangle's longest edge is at most the local size
    (raises past ``max_elements`` triangles); ``smooth_iterations`` rounds of
    Laplacian smoothing move interior nodes only. Triangle quality is whatever
    ear clipping gives; use gmsh for CAD-driven meshes."""
    poly = [(float(v[0]), float(v[1])) for v in vertices]
    if not 0 <= refinements <= 6:
        raise ValueError("refinements must be between 0 and 6")
    hole_loops = [[(float(v[0]), float(v[1])) for v in h] for h in (holes or [])]
    for k, hole in enumerate(hole_loops):
        if len(hole) < 3:
            raise ValueError(f"hole {k} needs at least 3 vertices")
        if abs(_signed_area(hole)) < 1e-14:
            raise ValueError(f"hole {k} has zero area")
        if any(not _point_in_polygon(p, poly) for p in hole):
            raise ValueError(f"hole {k} is not strictly inside the polygon")
    if hole_loops:
        poly = _bridge_holes(poly, hole_loops)
        walls = ([(vertices[i], vertices[(i + 1) % len(vertices)]) for i in range(len(vertices))] +
                 [(h[i], h[(i + 1) % len(h)]) for h in (holes or []) for i in range(len(h))])
        tris = ear_clip(poly, walls=walls)
    else:
        tris = ear_clip(poly)
    points = list(poly)
    if target_size is not None:
        if target_size <= 0:
            raise ValueError("target_size must be positive")
        longest = 0.0
        for a, b, c in tris:
            for u, v in ((a, b), (b, c), (c, a)):
                longest = max(longest, math.dist(points[u], points[v]))
        extra = 0
        while longest / (2 ** extra) > target_size and refinements + extra < 6:
            extra += 1
        refinements += extra
    mid_cache: dict[tuple[int, int], int] = {}

    def midpoint(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in mid_cache:
            points.append(((points[a][0] + points[b][0]) / 2, (points[a][1] + points[b][1]) / 2))
            mid_cache[key] = len(points) - 1
        return mid_cache[key]

    for _ in range(refinements):
        new = []
        for a, b, c in tris:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        tris = new
    # Collapse repeated points to single nodes (bridged hole loops repeat the
    # slit vertices; subdivision may also land on existing vertices). Degenerate
    # triangles along the slit are dropped.
    seen: dict[tuple[float, float], int] = {}
    remap: list[int] = []
    unique: list[tuple[float, float]] = []
    for p in points:
        if p in seen:
            remap.append(seen[p])
        else:
            seen[p] = len(unique)
            unique.append(p)
            remap.append(seen[p])
    tris = [(remap[a], remap[b], remap[c]) for a, b, c in tris]
    tris = [t for t in tris if len(set(t)) == 3]
    points = unique
    if not isinstance(max_elements, int) or isinstance(max_elements, bool) or max_elements < 1:
        raise ValueError("max_elements must be a positive integer")
    if not isinstance(smooth_iterations, int) or isinstance(smooth_iterations, bool) \
            or not 0 <= smooth_iterations <= 100:
        raise ValueError("smooth_iterations must be an integer between 0 and 100")
    if size_field is not None or size_sources:
        points, tris = grade_triangles(points, tris, size_function(size_field, size_sources),
                                       max_elements)
    if smooth_iterations:
        points = smooth_interior(points, tris, smooth_iterations)
    m = Mdpa()
    for i, (x, y) in enumerate(points):
        m.nodes[i + 1] = (x, y, 0.0)
    elems = m.elements.setdefault(element_name, {})
    for k, tri in enumerate(tris):
        elems[k + 1] = (property_id, [i + 1 for i in tri])
    # boundary: free edges, assigned to the outer side or hole loop containing them
    counts: dict[tuple[int, int], int] = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            counts[key] = counts.get(key, 0) + 1
    sides: dict[str, list[list[int]]] = {f"side_{i}": [] for i in range(len(vertices))}
    sides.update({f"hole_{k}": [] for k in range(len(hole_loops))})
    for tri in tris:
        for e in zip(tri, tri[1:] + tri[:1]):
            if counts[(min(e), max(e))] != 1:
                continue
            pa, pb = np.array(points[e[0]]), np.array(points[e[1]])
            mid = (pa + pb) / 2
            for name, (s0, s1) in _boundary_segments(vertices, holes):
                s0a, s1a = np.array(s0), np.array(s1)
                d = s1a - s0a
                denom = float(d @ d)
                if denom == 0:
                    continue
                t = float((mid - s0a) @ d / denom)
                dist = float(np.linalg.norm(s0a + t * d - mid))
                if 0 <= t <= 1 and dist <= 1e-9 * max(1.0, float(np.linalg.norm(d))):
                    sides[name].append([e[0] + 1, e[1] + 1])
                    break
    return _attach_boundaries(m, {k: v for k, v in sides.items() if v}, condition_name,
                              property_id, root_part)


def _boundary_segments(vertices: list[list[float]], holes: list[list[list[float]]] | None
                       ) -> list[tuple[str, tuple[tuple[float, float], tuple[float, float]]]]:
    out = []
    for i in range(len(vertices)):
        a, b = vertices[i], vertices[(i + 1) % len(vertices)]
        out.append((f"side_{i}", ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))))
    for k, hole in enumerate(holes or []):
        for j in range(len(hole)):
            a, b = hole[j], hole[(j + 1) % len(hole)]
            out.append((f"hole_{k}", ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))))
    return out


GENERATORS: dict[str, Any] = {
    "annulus": create_annulus_mesh,
    "disc": create_disc_mesh,
    "cylinder": create_cylinder_mesh,
    "sphere": create_sphere_mesh,
    "polygon": create_polygon_mesh,
}
