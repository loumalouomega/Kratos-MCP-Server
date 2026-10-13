"""Unit tests for kratos_mcp.geometry generators (pure Python)."""

from __future__ import annotations

import math

import pytest

from kratos_mcp import geometry as geo, mdpa as mdpa_mod, mesh_edit as me


def area(m):
    return sum(me.measure(m, t, c) for t, by in m.elements.items() for _, c in by.values())


def test_annulus_full_and_sector():
    m = geo.create_annulus_mesh(1.0, 2.0, 4, 64)
    assert m.validate() == []
    assert area(m) == pytest.approx(math.pi * 3, rel=5e-3)
    assert set(m.sub_model_parts) == {"domain", "inner", "outer"}
    assert len(m.sub_model_parts["inner"].conditions) == 64
    sector = geo.create_annulus_mesh(1.0, 2.0, 2, 16, angle=90, triangles=True)
    assert sector.validate() == []
    assert area(sector) == pytest.approx(math.pi * 3 / 4, rel=2e-2)
    assert {"start", "end"} <= set(sector.sub_model_parts)
    with pytest.raises(ValueError):
        geo.create_annulus_mesh(2.0, 1.0, 2, 8)


def test_disc():
    m = geo.create_disc_mesh(1.0, 6, 48)
    assert m.validate() == []
    assert area(m) == pytest.approx(math.pi, rel=1e-2)
    assert me.quality(m)["elements"]["SmallDisplacementElement2D3N"]["measure"]["min"] > 0
    assert len(m.sub_model_parts["boundary"].conditions) == 48


def test_cylinders_and_sphere():
    solid = geo.create_cylinder_mesh(1.0, 2.0, 3, 24, 4)
    assert solid.validate() == []
    q = me.quality(solid)["elements"]["SmallDisplacementElement3D6N"]
    assert "non_positive_or_invalid" not in q
    assert q["measure"]["total"] == pytest.approx(math.pi * 2, rel=2e-2)
    assert {"lateral", "zmin", "zmax"} <= set(solid.sub_model_parts)
    for t, by in solid.conditions.items():
        for _, c in by.values():
            assert len(c) == mdpa_mod.expected_node_count(t)
    hollow = geo.create_cylinder_mesh(2.0, 1.0, 2, 24, 2, inner_radius=1.0)
    assert hollow.validate() == []
    assert me.quality(hollow)["elements"]["SmallDisplacementElement3D8N"]["measure"]["total"] \
        == pytest.approx(math.pi * 3, rel=2e-2)
    ball = geo.create_sphere_mesh(1.5, 4)
    assert ball.validate() == []
    q = me.quality(ball)["elements"]["SmallDisplacementElement3D8N"]
    assert "non_positive_or_invalid" not in q
    assert q["measure"]["total"] == pytest.approx(4 / 3 * math.pi * 1.5 ** 3, rel=0.15)
    assert max(math.dist(p, (0, 0, 0)) for p in ball.nodes.values()) == pytest.approx(1.5)
    assert len(ball.sub_model_parts["surface"].conditions) == 6 * 16


def test_polygon_convex_and_concave():
    square = geo.create_polygon_mesh([[0, 0], [2, 0], [2, 2], [0, 2]], refinements=2)
    assert square.validate() == []
    assert area(square) == pytest.approx(4.0)
    assert len(square.sub_model_parts["side_0"].conditions) == 4
    # L shape, given clockwise
    ell = geo.create_polygon_mesh([[0, 0], [0, 2], [1, 2], [1, 1], [2, 1], [2, 0]], refinements=1)
    assert ell.validate() == []
    assert area(ell) == pytest.approx(3.0)
    assert sum(len(p.conditions) for n, p in ell.sub_model_parts.items()
               if n.startswith("side_")) == 6 * 2
    with pytest.raises(ValueError):
        geo.create_polygon_mesh([[0, 0], [1, 1]])
    with pytest.raises(ValueError):
        geo.create_polygon_mesh([[0, 0], [1, 1], [1, 0], [0, 1]])  # bow tie


def test_polygon_with_hole():
    hole = [[1, 0.5], [2, 0.5], [2, 1.5], [1, 1.5]]
    m = geo.create_polygon_mesh([[0, 0], [4, 0], [4, 2], [0, 2]], holes=[hole])
    assert m.validate() == []
    assert area(m) == pytest.approx(8.0 - 1.0)
    assert set(m.sub_model_parts) == {"domain", "side_0", "side_1", "side_2", "side_3",
                                      "hole_0"}
    assert len(m.sub_model_parts["hole_0"].conditions) == 4
    assert me.quality(m)["elements"]["SmallDisplacementElement2D3N"]["measure"]["min"] > 0
    # hole boundary nodes sit exactly on the hole loop
    ring = m.sub_model_parts["hole_0"].nodes
    assert len(ring) == 4
    for nid in ring:
        x, y, _ = m.nodes[nid]
        assert min(abs(x - 1), abs(x - 2), abs(y - 0.5), abs(y - 1.5)) < 1e-9
    # clockwise inputs work too
    cw = geo.create_polygon_mesh([[0, 0], [0, 2], [4, 2], [4, 0]],
                                 holes=[[[1, 0.5], [1, 1.5], [2, 1.5], [2, 0.5]]])
    assert cw.validate() == [] and area(cw) == pytest.approx(7.0)
    # two holes
    two = geo.create_polygon_mesh(
        [[0, 0], [4, 0], [4, 2], [0, 2]],
        holes=[[[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]],
               [[2.5, 0.5], [3.0, 0.5], [3.0, 1.0], [2.5, 1.0]]])
    assert two.validate() == [] and area(two) == pytest.approx(8.0 - 0.5)
    assert {"hole_0", "hole_1"} <= set(two.sub_model_parts)
    with pytest.raises(ValueError, match="inside"):
        geo.create_polygon_mesh([[0, 0], [1, 0], [1, 1], [0, 1]],
                                holes=[[[5, 5], [6, 5], [6, 6]]])
    with pytest.raises(ValueError, match="at least 3"):
        geo.create_polygon_mesh([[0, 0], [1, 0], [1, 1], [0, 1]], holes=[[[0, 0], [1, 1]]])


def test_polygon_target_size():
    m = geo.create_polygon_mesh([[0, 0], [4, 0], [4, 2], [0, 2]], target_size=0.5)
    assert m.validate() == []
    longest = max(d for t, by in m.elements.items() for _, c in by.values()
                  for d in me.edge_lengths(m, t, c))
    assert longest <= 0.5 * (1 + 1e-9)
    assert area(m) == pytest.approx(8.0)
    with pytest.raises(ValueError, match="target_size"):
        geo.create_polygon_mesh([[0, 0], [1, 0], [0, 1]], target_size=0.0)


# ------------------------------------------------------- graded polygon meshes

SQUARE = [[0, 0], [4, 0], [4, 2], [0, 2]]


def edge_use(m):
    """Undirected edge -> number of triangles using it."""
    use: dict[tuple[int, int], int] = {}
    for by in m.elements.values():
        for _, conn in by.values():
            for a, b in zip(conn, conn[1:] + conn[:1]):
                use[(min(a, b), max(a, b))] = use.get((min(a, b), max(a, b)), 0) + 1
    return use


def assert_conforming(m):
    # every edge is shared by at most 2 triangles, and the free edges are exactly
    # the boundary conditions (no hanging nodes)
    use = edge_use(m)
    assert set(use.values()) <= {1, 2}
    free = {k for k, v in use.items() if v == 1}
    conds = {(min(c), max(c)) for by in m.conditions.values() for _, c in by.values()}
    assert free == conds


def local_size(m, point):
    near = []
    for t, by in m.elements.items():
        for _, c in by.values():
            cx = sum(m.nodes[i][0] for i in c) / 3
            cy = sum(m.nodes[i][1] for i in c) / 3
            if math.dist((cx, cy), point) < 0.3:
                near.append(max(me.edge_lengths(m, t, c)))
    return sum(near) / len(near)


def test_graded_point_source_is_conforming_and_graded():
    m = geo.create_polygon_mesh(SQUARE, size_sources=[
        {"point": [0.0, 0.0], "size": 0.08, "growth": 0.4}], target_size=2.0)
    assert m.validate() == []
    assert area(m) == pytest.approx(8.0)
    assert_conforming(m)
    assert "non_positive_or_invalid" not in me.quality(m)["elements"][
        "SmallDisplacementElement2D3N"]
    near, far = local_size(m, (0.2, 0.2)), local_size(m, (3.5, 1.7))
    assert near < 0.2 and far > 3 * near
    longest = max(d for t, by in m.elements.items() for _, c in by.values()
                  for d in me.edge_lengths(m, t, c))
    assert longest > 1.0  # far field stays coarse: not a uniform refinement
    assert {"domain", "side_0", "side_1", "side_2", "side_3"} <= set(m.sub_model_parts)


def test_graded_size_field_expression_with_hole_and_smoothing():
    hole = [[[1.0, 0.5], [2.0, 0.5], [2.0, 1.5], [1.0, 1.5]]]
    m = geo.create_polygon_mesh(SQUARE, holes=hole, size_field="0.15 + 0.3*abs(x - 3)",
                                target_size=1.0, smooth_iterations=3)
    assert m.validate() == []
    assert area(m) == pytest.approx(7.0)
    assert_conforming(m)
    assert len(m.sub_model_parts["hole_0"].conditions) >= 4
    assert local_size(m, (3.0, 1.0)) < local_size(m, (0.2, 1.8))
    # smoothing never flips a triangle
    assert "non_positive_or_invalid" not in me.quality(m)["elements"][
        "SmallDisplacementElement2D3N"]


def test_segment_source_and_combined_sources():
    m = geo.create_polygon_mesh(SQUARE, size_field="0.5", size_sources=[
        {"segment": [[0, 0], [4, 0]], "size": 0.1, "growth": 0.5}])
    assert m.validate() == []
    assert_conforming(m)
    assert local_size(m, (2.0, 0.1)) < local_size(m, (2.0, 1.9)) <= 0.5 * (1 + 1e-9)


def test_grading_errors():
    with pytest.raises(ValueError, match="max_elements"):
        geo.create_polygon_mesh(SQUARE, size_field="0.01", max_elements=500)
    with pytest.raises(ValueError, match="size_field"):
        geo.create_polygon_mesh(SQUARE, size_field="1/(x - x)")
    with pytest.raises(ValueError, match="positive"):
        geo.create_polygon_mesh(SQUARE, size_field="-1")
    with pytest.raises(ValueError, match="allowed"):
        geo.create_polygon_mesh(SQUARE, size_field="__import__('os')")
    with pytest.raises(ValueError, match="exactly one"):
        geo.create_polygon_mesh(SQUARE, size_sources=[{"size": 1.0}])
    with pytest.raises(ValueError, match="size must be positive"):
        geo.create_polygon_mesh(SQUARE, size_sources=[{"point": [0, 0], "size": 0}])
    with pytest.raises(ValueError, match="smooth_iterations"):
        geo.create_polygon_mesh(SQUARE, size_field="1", smooth_iterations=-1)


def min_angle(m):
    return me.quality(m)["elements"]["SmallDisplacementElement2D3N"]["angle_degrees"]["min"]


def test_quality_reports_corner_angles():
    m = mdpa_mod.create_rectangle_mesh(1.0, 1.0, 1, 1, element_name="Element2D3N",
                                       condition_name="LineCondition2D2N", triangles=True)
    angles = me.quality(m)["elements"]["Element2D3N"]["angle_degrees"]
    assert angles["min"] == pytest.approx(45.0) and angles["max"] == pytest.approx(90.0)
    quad = mdpa_mod.create_rectangle_mesh(1.0, 1.0, 1, 1)
    q = next(iter(me.quality(quad)["elements"].values()))["angle_degrees"]
    assert q["min"] == pytest.approx(90.0) and q["max"] == pytest.approx(90.0)


def test_flips_and_smoothing_keep_conformity_and_never_worsen_the_graded_mesh():
    kw = dict(size_field="0.15 + 0.3*abs(x - 3)", target_size=1.0,
              holes=[[[1.0, 0.5], [2.0, 0.5], [2.0, 1.5], [1.0, 1.5]]])
    raw = geo.create_polygon_mesh(SQUARE, **kw)
    polished = geo.create_polygon_mesh(SQUARE, smooth_iterations=3, **kw)
    assert polished.validate() == []
    assert_conforming(polished)
    assert area(polished) == pytest.approx(area(raw))
    assert len(polished.nodes) == len(raw.nodes)
    assert min_angle(polished) >= min_angle(raw) - 1e-9
    assert len(polished.sub_model_parts["hole_0"].conditions) == len(
        raw.sub_model_parts["hole_0"].conditions)


def test_delaunay_flip_repairs_a_sliver_pair():
    pts = [(0.0, 0.0), (4.0, 0.0), (2.0, 0.1), (2.0, -0.1)]
    # diagonal 0-1 is long and thin; the Delaunay diagonal is 2-3
    tris = geo.flip_edges(pts, [(0, 1, 2), (1, 0, 3)])
    assert {frozenset(t) for t in tris} == {frozenset((0, 3, 2)), frozenset((3, 1, 2))}
    assert all(((pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1])
                - (pts[c][0] - pts[a][0]) * (pts[b][1] - pts[a][1])) > 0 for a, b, c in tris)
