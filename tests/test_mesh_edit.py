"""Unit tests for kratos_mcp.mesh_edit (pure Python, no Kratos)."""

from __future__ import annotations

import pytest

from kratos_mcp import mdpa as mdpa_mod, mesh_edit as me


def rect(nx=4, ny=2, tri=False):
    return mdpa_mod.create_rectangle_mesh(4.0, 2.0, nx, ny, triangles=tri,
                                          element_name="SmallDisplacementElement2D3N" if tri
                                          else "SmallDisplacementElement2D4N")


def total(mesh, kind="elements"):
    return sum(me.measure(mesh, t, c) for t, by in getattr(mesh, kind).items()
               for _, c in by.values())


# ---------------------------------------------------------------- geometries

def test_geometries_round_trip_and_validate():
    m = rect()
    m.geometries["Triangle2D3"] = {1: [1, 2, 7], 2: [2, 8, 7]}
    m.sub_model_parts["g"] = mdpa_mod.SubModelPart("g", nodes=[1, 2, 7, 8], geometries=[1, 2])
    again = mdpa_mod.parse(m.dumps())
    assert again.geometries == m.geometries
    assert again.sub_model_parts["g"].geometries == [1, 2]
    assert again.inspect()["num_geometries"] == 2
    assert again.validate() == []
    m.geometries["Triangle2D3"][3] = [1, 2, 999]
    assert any("Geometry 3" in i for i in m.validate())


def test_validate_connectivity_length_and_duplicate_ids():
    m = rect()
    m.elements["SmallDisplacementElement2D4N"][1] = (1, [1, 2, 3])
    assert any("implies 4" in i for i in m.validate())
    m = rect()
    m.elements["Other2D3N"] = {1: (1, [1, 2, 7])}
    assert any("Duplicate element id 1" in i for i in m.validate())


# ------------------------------------------------------------------ selection

def test_select_nodes_combinations():
    m = rect()
    assert me.select_nodes(m, {"plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}}) == [1, 6, 11]
    assert len(me.select_nodes(m, {"box": {"min": [0, 0, 0], "max": [1, 2, 0]}})) == 6
    assert me.select_nodes(m, {"sphere": {"center": [0, 0], "radius": 0.1}}) == [1]
    assert me.select_nodes(m, {"expression": "x - 1.0", "part": "bottom"}) == [1, 2]
    assert me.select_nodes(m, {"ids": [1, 2], "box": {"min": [0, 0, 0], "max": [0.5, 1, 0]}}) == [1]
    with pytest.raises(ValueError):
        me.select_nodes(m, {})
    with pytest.raises(ValueError):
        me.select_nodes(m, {"bogus": 1})
    with pytest.raises(ValueError):
        me.select_nodes(m, {"ids": [9999]})


# ---------------------------------------------------------------- nodes/entities

def test_add_move_delete_nodes():
    m = rect()
    n = len(m.nodes)
    ids = me.add_nodes(m, [[10, 10], [11, 10, 1]], part="bottom")
    assert ids == [n + 1, n + 2] and m.nodes[n + 2] == (11.0, 10.0, 1.0)
    assert ids[0] in m.sub_model_parts["bottom"].nodes
    with pytest.raises(ValueError):
        me.add_nodes(m, [[0, 0]], ids=[1])
    me.move_nodes(m, [1], offset=[0.5, 0, 0])
    assert m.nodes[1][0] == 0.5
    me.move_nodes(m, [1], coordinates=[[0, 0, 0]])
    with pytest.raises(ValueError):
        me.move_nodes(m, [1])
    with pytest.raises(ValueError, match="cascade"):
        me.delete_nodes(m, [1])
    out = me.delete_nodes(m, [1], cascade=True)
    assert out["element"] == 1 and out["condition"] == 2
    assert 1 not in m.nodes and m.validate() == []
    assert 1 not in m.sub_model_parts["left"].nodes


def test_entities_add_delete_retype():
    m = rect()
    ids = me.add_entities(m, "element", "SmallDisplacementElement2D4N",
                          [{"nodes": [1, 2, 8, 7], "property_id": 2}], part="domain")
    assert ids == [9]
    assert ids[0] in m.sub_model_parts["domain"].elements
    with pytest.raises(ValueError, match="needs 4"):
        me.add_entities(m, "element", "SmallDisplacementElement2D4N", [{"nodes": [1, 2, 3]}])
    with pytest.raises(ValueError, match="missing nodes"):
        me.add_entities(m, "condition", "LineCondition2D2N", [{"nodes": [1, 999]}])
    gid = me.add_entities(m, "geometry", "Line2D2", [{"nodes": [1, 2]}])
    assert gid == [1] and m.geometries["Line2D2"][1] == [1, 2]
    assert me.set_entity_properties(m, "element", [1, 9], property_id=5) == 2
    assert m.elements["SmallDisplacementElement2D4N"][9][0] == 5
    me.set_entity_properties(m, "element", [1], new_type="Other2D4N")
    assert 1 in m.elements["Other2D4N"]
    with pytest.raises(ValueError):
        me.set_entity_properties(m, "element", [2], new_type="Tri2D3N")
    assert me.delete_entities(m, "element", [1, 9]) == 2
    assert "Other2D4N" not in m.elements
    assert 1 not in m.sub_model_parts["domain"].elements


# ------------------------------------------------------------ sub model parts

def test_sub_model_part_ops():
    m = rect()
    sel = me.select_nodes(m, {"plane": {"point": [4, 0, 0], "normal": [1, 0, 0]}})
    part = me.create_part(m, "domain.tip", nodes=sel, include=["condition"])
    assert part.nodes == sel and len(part.conditions) == 2
    assert set(sel) <= set(m.sub_model_parts["domain"].nodes)
    with pytest.raises(ValueError):
        me.create_part(m, "domain.tip")
    me.create_part(m, "domain.tip", exist_ok=True)
    with pytest.raises(ValueError, match="not found"):
        me.create_part(m, "nope.child")
    me.remove_members(m, "domain", nodes=[sel[0]])
    assert sel[0] not in m.sub_model_parts["domain"].children["tip"].nodes
    assert me.rename_part(m, "domain.tip", "end") == "domain.end"
    assert "end" in m.sub_model_parts["domain"].children
    me.delete_part(m, "domain.end")
    assert not m.sub_model_parts["domain"].children
    with pytest.raises(ValueError):
        me.rename_part(m, "left", "bottom")
    assert mdpa_mod.parse(m.dumps()).validate() == []


def test_add_members_adds_entity_nodes():
    m = rect()
    me.create_part(m, "p")
    me.add_members(m, "p", elements=[1])
    assert set(m.sub_model_parts["p"].nodes) == {1, 2, 6, 7}


# ------------------------------------------------------------------ transforms

def test_transform_translate_rotate_scale():
    m = rect()
    me.transform(m, "translate", vector=[1, 2, 3])
    assert m.nodes[1] == (1.0, 2.0, 3.0)
    m = rect()
    me.transform(m, "rotate", angle=90, origin=[0, 0, 0])
    x, y, _ = m.nodes[2]  # was (1, 0)
    assert (round(x, 9), round(y, 9)) == (0.0, 1.0)
    m = rect()
    area = total(m)
    me.transform(m, "scale", factors=2.0)
    assert total(m) == pytest.approx(4 * area)
    with pytest.raises(ValueError):
        me.transform(m, "scale", factors=0)
    with pytest.raises(ValueError):
        me.transform(m, "spin")


@pytest.mark.parametrize("tri", [False, True])
def test_mirror_keeps_positive_measure(tri):
    m = rect(tri=tri)
    me.transform(m, "mirror", point=[0, 0, 0], normal=[1, 0, 0])
    assert min(me.measure(m, t, c) for t, by in m.elements.items() for _, c in by.values()) > 0
    assert m.bounding_box()["min"][0] == -4.0


def test_mirror_box_hex_positive():
    m = mdpa_mod.create_box_mesh(1, 1, 1, 2, 2, 2)
    me.transform(m, "mirror", point=[0, 0, 0], normal=[0, 0, 1])
    q = me.quality(m)
    assert "non_positive_or_invalid" not in q["elements"]["SmallDisplacementElement3D8N"]
    assert q["elements"]["SmallDisplacementElement3D8N"]["measure"]["total"] == pytest.approx(1.0)


# ----------------------------------------------------------------- merge etc.

def test_merge_coincident_nodes_and_meshes():
    a = mdpa_mod.create_rectangle_mesh(1, 1, 2, 2)
    b = mdpa_mod.create_rectangle_mesh(1, 1, 2, 2)
    me.transform(b, "translate", vector=[1, 0, 0])
    info = me.merge_meshes(a, b, merge_tolerance=1e-9)
    assert info["merge"]["merged"] == 3
    assert len(a.nodes) == 9 + 9 - 3
    assert sum(len(v) for v in a.elements.values()) == 8
    assert a.validate() == []
    assert total(a) == pytest.approx(2.0)
    assert a.sub_model_parts["domain"].elements and len(a.sub_model_parts["domain"].elements) == 8
    with pytest.raises(ValueError):
        me.merge_coincident_nodes(a, 0)


def test_renumber_and_extract():
    m = rect()
    me.delete_nodes(m, [1], cascade=True)
    info = me.renumber(m, start=1)
    assert info["nodes"] == {"first": 1, "last": len(m.nodes)}
    assert sorted(m.nodes) == list(range(1, len(m.nodes) + 1))
    assert m.validate() == []
    sub = me.extract_part(m, "right")
    assert len(sub.nodes) == 3 and sum(len(v) for v in sub.conditions.values()) == 2
    assert not sub.elements and "right" in sub.sub_model_parts
    assert sub.validate() == []


# ------------------------------------------------------------------ conversion

def test_quad_to_tri_preserves_area_and_parts():
    m = rect()
    area = total(m)
    out = me.convert_cells(m, "quad_to_tri")
    assert out["elements"] == 8
    assert list(m.elements) == ["SmallDisplacementElement2D3N"]
    assert len(m.elements["SmallDisplacementElement2D3N"]) == 16
    assert total(m) == pytest.approx(area)
    assert len(m.sub_model_parts["domain"].elements) == 16
    assert m.validate() == []
    assert me.quality(m)["elements"]["SmallDisplacementElement2D3N"]["measure"]["min"] > 0


def test_hex_to_tet_preserves_volume_and_boundary():
    m = mdpa_mod.create_box_mesh(2, 1, 1, 2, 1, 1)
    vol = total(m)
    out = me.convert_cells(m, "hex_to_tet")
    assert out["elements"] == 2 and out["conditions"] == 10
    assert total(m) == pytest.approx(vol)
    assert len(m.elements["SmallDisplacementElement3D4N"]) == 12
    assert m.validate() == []
    q = me.quality(m)
    assert "non_positive_or_invalid" not in q["elements"]["SmallDisplacementElement3D4N"]
    # conforming: every boundary triangle must be a face of exactly one tet, interior faces two
    faces = {}
    for _, (_, c) in m.elements["SmallDisplacementElement3D4N"].items():
        for f in me.FACES["tet"]:
            key = frozenset(c[i] for i in f)
            faces[key] = faces.get(key, 0) + 1
    for _, (_, c) in m.conditions["SurfaceLoadCondition3D3N"].items():
        assert faces[frozenset(c)] == 1
    assert sum(1 for v in faces.values() if v == 1) == 20


# --------------------------------------------------------------------- boundary

def test_extract_boundary_2d_and_3d():
    m = rect()
    m.conditions.clear()
    for part in m.sub_model_parts.values():
        part.conditions.clear()
    out = me.extract_boundary(m, "LineLoadCondition2D2N", "outline")
    assert out["conditions_created"] == 12
    assert len(m.sub_model_parts["outline"].conditions) == 12
    # outward: domain on the left of each edge => cross(edge, centroid - a) > 0
    for _, (_, c) in m.conditions["LineLoadCondition2D2N"].items():
        a, b = (m.nodes[n] for n in c)
        cx, cy = 2.0, 1.0
        assert (b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0]) > 0
    only_left = me.extract_boundary(
        m, "LineCondition2D2N", "leftedge", node_selector={"plane": {
            "point": [0, 0, 0], "normal": [1, 0, 0]}})
    assert only_left["conditions_created"] == 2

    box = mdpa_mod.create_box_mesh(1, 1, 1, 2, 2, 2)
    box.conditions.clear()
    for part in box.sub_model_parts.values():
        part.conditions.clear()
    out = me.extract_boundary(box, "SurfaceCondition3D4N", "skin")
    assert out["conditions_created"] == 24
    # outward normal check: each face normal points away from the cube centre
    import numpy as np
    for _, (_, c) in box.conditions["SurfaceCondition3D4N"].items():
        p = np.array([box.nodes[n] for n in c])
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        assert normal @ (p.mean(axis=0) - np.array([0.5, 0.5, 0.5])) > 0
    with pytest.raises(ValueError, match="No condition name"):
        me.extract_boundary(box, {"3": "X"}, "skin2")


def test_extract_boundary_2d_orientation_is_counter_clockwise():
    m = mdpa_mod.create_rectangle_mesh(1, 1, 1, 1, condition_name=None)
    me.extract_boundary(m, "LineCondition2D2N", "outline")
    edges = [tuple(c) for _, c in m.conditions["LineCondition2D2N"].values()]
    assert set(edges) == {(1, 2), (2, 4), (4, 3), (3, 1)}


# ----------------------------------------------------------------------- extrude

def test_extrude_quads_and_triangles():
    m = rect(nx=2, ny=1)
    area = total(m)
    out = me.extrude(m, [0.5, 0.5],
                     condition_names={"LineLoadCondition2D2N": "SurfaceLoadCondition3D4N"},
                     cap_condition_names={"4": "SurfaceCondition3D4N"})
    assert len(out.nodes) == 6 * 3
    assert len(out.elements["SmallDisplacementElement3D8N"]) == 4
    assert total(out) == pytest.approx(area * 1.0)
    assert out.validate() == []
    assert len(out.sub_model_parts["zmax"].conditions) == 2
    assert len(out.conditions["SurfaceLoadCondition3D4N"]) == 12
    assert out.bounding_box()["max"][2] == pytest.approx(1.0)
    q = me.quality(out)
    assert "non_positive_or_invalid" not in q["elements"]["SmallDisplacementElement3D8N"]

    t = rect(nx=2, ny=1, tri=True)
    out = me.extrude(t, [1.0])
    assert len(out.elements["SmallDisplacementElement3D6N"]) == 4
    assert total(out) == pytest.approx(total(t))
    assert out.validate() == []

    with pytest.raises(ValueError):
        me.extrude(out, [1.0])
    with pytest.raises(ValueError):
        me.extrude(m, [])


# ------------------------------------------------------------- properties, data

def test_properties_and_nodal_data():
    m = rect()
    props = me.set_properties(m, 3, {"YOUNG_MODULUS": 2.1e11, "G": [0, -9.81]})
    assert props == {"YOUNG_MODULUS": "210000000000.0", "G": "0 -9.81"}
    assert me.set_properties(m, 3, remove=["G"]) == {"YOUNG_MODULUS": "210000000000.0"}
    assert me.set_nodal_data(m, "TEMPERATURE", [1, 2], 5.0) == 2
    again = mdpa_mod.parse(m.dumps())
    assert again.nodal_data["TEMPERATURE"] == {1: 5.0, 2: 5.0}
    with pytest.raises(ValueError):
        me.set_nodal_data(m, "T", [9999], 1.0)


def test_quality_flags_inverted():
    m = rect()
    m.nodes[3] = (-5.0, 5.0, 0.0)  # drag a node through its neighbours
    q = me.quality(m)["elements"]["SmallDisplacementElement2D4N"]
    assert q["non_positive_or_invalid"]["count"] >= 1
    assert q["aspect_ratio"]["max"] > 1
