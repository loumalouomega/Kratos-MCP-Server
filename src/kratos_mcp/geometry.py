"""Kratos-free parametric geometry generators beyond the structured box.

All of them return an :class:`~kratos_mcp.mdpa.Mdpa` that follows the naming
convention of ``create_rectangle_mesh``: a ``domain`` part with every node and
element plus named boundary parts holding the boundary nodes and (when a
condition type is given) the boundary conditions. Arbitrary shapes with holes
or CAD input need the optional gmsh route (see ``mesh_io.gmsh_generate``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import mesh_edit
from .mdpa import Mdpa, SubModelPart


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


def ear_clip(poly: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon (counter-clockwise or clockwise, no holes)
    by ear clipping; returns index triples, counter-clockwise."""
    n = len(poly)
    if n < 3:
        raise ValueError("a polygon needs at least 3 vertices")
    idx = list(range(n))
    if abs(_signed_area(poly)) < 1e-14:
        raise ValueError("the polygon has zero area (or is self-intersecting)")
    _check_simple(poly)
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
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            raise ValueError("could not triangulate: the polygon is self-intersecting or degenerate")
    tris.append((idx[0], idx[1], idx[2]))
    return tris


def create_polygon_mesh(vertices: list[list[float]], refinements: int = 0,
                        element_name: str = "SmallDisplacementElement2D3N",
                        condition_name: str | None = "LineLoadCondition2D2N",
                        property_id: int = 1, root_part: str = "domain") -> Mdpa:
    """Triangle mesh of a simple polygon (no holes): ear clipping, then
    ``refinements`` rounds of midpoint subdivision (each splits a triangle in 4).
    One boundary part ``side_<i>`` per polygon edge (edge i joins vertex i and
    i+1). Triangle quality is whatever ear clipping gives; use gmsh for a
    graded, good-quality mesh."""
    poly = [(float(v[0]), float(v[1])) for v in vertices]
    if not 0 <= refinements <= 6:
        raise ValueError("refinements must be between 0 and 6")
    tris = ear_clip(poly)
    points = list(poly)
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
    m = Mdpa()
    for i, (x, y) in enumerate(points):
        m.nodes[i + 1] = (x, y, 0.0)
    elems = m.elements.setdefault(element_name, {})
    for k, tri in enumerate(tris):
        elems[k + 1] = (property_id, [i + 1 for i in tri])
    # boundary: free edges, assigned to the polygon side that contains them
    counts: dict[tuple[int, int], int] = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            counts[key] = counts.get(key, 0) + 1
    sides: dict[str, list[list[int]]] = {f"side_{i}": [] for i in range(len(poly))}
    for tri in tris:
        for e in zip(tri, tri[1:] + tri[:1]):
            if counts[(min(e), max(e))] != 1:
                continue
            pa, pb = np.array(points[e[0]]), np.array(points[e[1]])
            mid = (pa + pb) / 2
            for i in range(len(poly)):
                s0, s1 = np.array(poly[i]), np.array(poly[(i + 1) % len(poly)])
                d = s1 - s0
                t = float((mid - s0) @ d / (d @ d))
                dist = float(np.linalg.norm(s0 + t * d - mid))
                if 0 <= t <= 1 and dist <= 1e-9 * max(1.0, float(np.linalg.norm(d))):
                    sides[f"side_{i}"].append([e[0] + 1, e[1] + 1])
                    break
    return _attach_boundaries(m, {k: v for k, v in sides.items() if v}, condition_name,
                              property_id, root_part)


GENERATORS: dict[str, Any] = {
    "annulus": create_annulus_mesh,
    "disc": create_disc_mesh,
    "cylinder": create_cylinder_mesh,
    "sphere": create_sphere_mesh,
    "polygon": create_polygon_mesh,
}
