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
