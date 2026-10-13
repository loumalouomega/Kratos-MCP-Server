"""Unit tests for kratos_mcp.mesh_edit (pure Python, no Kratos)."""

from __future__ import annotations

import numpy as np
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


# ------------------------------------------------------- data block rewrites

def data_mesh():
    m = rect()
    me.set_nodal_data(m, "TEMP", [1, 2], 5.0, fixed=True)
    me.set_nodal_data(m, "DISP", [1], "[3](1.0, 0.0, 0.0)")
    me.set_elemental_data(m, "elemental", "DENSITY", {1: 7850.0, 2: "[3](0, 1, 0)"})
    me.set_elemental_data(m, "conditional", "X", {1: 3.0})
    me.set_table(m, 0, "TIME", "VELOCITY", [[0, 0], [1, 2.5]])
    m.nodal_flags["ACTIVE"] = [1, 2]
    m.sub_model_parts["left"].tables.append(0)
    m.sub_model_parts["left"].data["FLAG"] = "on"
    return m


def test_data_blocks_follow_renumber():
    m = data_mesh()
    me.renumber(m, start=10)
    assert set(m.elemental_data["DENSITY"]) <= set(m.element_ids())
    assert set(m.conditional_data["X"]) <= set(m.condition_ids())
    assert set(m.nodal_data["TEMP"]) <= set(m.nodes)
    assert m.nodal_data_fixed["TEMP"] == set(m.nodal_data["TEMP"])
    assert set(m.vector_nodal_data["DISP"]) <= set(m.nodes)
    assert set(m.nodal_flags["ACTIVE"]) <= set(m.nodes)
    assert m.sub_model_parts["left"].tables == [0]
    assert m.validate() == []
    assert mdpa_mod.parse(m.dumps()).validate() == []


def test_data_blocks_follow_delete_and_extract():
    m = data_mesh()
    e = min(m.element_ids())
    me.delete_entities(m, "element", [e])
    assert e not in m.elemental_data["DENSITY"]
    me.delete_nodes(m, [1], cascade=True)
    assert 1 not in m.nodal_data["TEMP"] and 1 not in m.vector_nodal_data["DISP"]
    assert 1 not in m.nodal_flags["ACTIVE"]
    sub = me.extract_part(m, "left")
    assert set(sub.tables) == {0}
    assert set(sub.elemental_data.get("DENSITY", {})) <= sub.element_ids()
    assert sub.validate() == []


def test_data_blocks_merge_with_table_collision():
    a, b = data_mesh(), data_mesh()
    me.set_table(b, 0, "TIME", "OTHER", [[0, 0]])
    me.merge_meshes(a, b)
    assert sorted(a.tables) == [0, 1]
    assert a.validate() == []
    assert mdpa_mod.parse(a.dumps()).validate() == []


def test_set_elemental_data_and_tables():
    m = rect()
    out = me.set_elemental_data(m, "elemental", "DENSITY", {1: 2.0, 2: [0, 1, 0]})
    assert out == {"updated": 2, "removed": 0}
    assert m.elemental_data["DENSITY"][2] == "0 1 0"
    with pytest.raises(ValueError, match="Missing element 999"):
        me.set_elemental_data(m, "elemental", "DENSITY", {999: 1.0})
    with pytest.raises(ValueError, match="kind"):
        me.set_elemental_data(m, "bogus", "D", {1: 1.0})
    out = me.set_elemental_data(m, "elemental", "DENSITY", {}, remove_ids=[1])
    assert out["removed"] == 1 and 1 not in m.elemental_data["DENSITY"]
    me.set_table(m, 3, "X", "Y", [[0, 1]])
    assert m.tables[3].rows == [(0.0, 1.0)]
    m.sub_model_parts["left"].tables.append(3)
    with pytest.raises(ValueError, match="referenced"):
        me.delete_table(m, 3)
    m.sub_model_parts["left"].tables.clear()
    me.delete_table(m, 3)
    with pytest.raises(ValueError, match="Missing table"):
        me.delete_table(m, 3)


# ------------------------------------------------------------- higher order

def tri6_mesh():
    m = mdpa_mod.Mdpa()
    m.nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (1, 1, 0), 4: (0, 1, 0),
               5: (0.5, 0, 0), 6: (1, 0.5, 0), 7: (0.5, 1, 0),
               8: (0, 0.5, 0), 9: (0.5, 0.5, 0)}
    m.elements["Element2D6N"] = {1: (1, [1, 2, 3, 5, 6, 9]),
                                 2: (1, [1, 3, 4, 9, 7, 8])}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m.nodes), elements=[1, 2])
    return m


def test_quadratic_quality_uses_corners():
    m = tri6_mesh()
    assert m.validate() == []
    q = me.quality(m)["elements"]["Element2D6N"]
    assert "skipped_unsupported" not in q
    assert q["measure"] == {"min": 0.5, "max": 0.5, "mean": 0.5, "total": 1.0}
    assert me.cell_kind(m, "Element2D6N", [1, 2, 3, 5, 6, 9]) == "tri"
    assert me.corner_nodes(m, "Element2D6N", [1, 2, 3, 5, 6, 9]) == [1, 2, 3]


def test_quadratic_boundary_and_linearize():
    m = tri6_mesh()
    out = me.extract_boundary(m, {"3": "LineCondition2D3N"}, "skin")
    assert out["conditions_created"] == 4
    assert all(len(c) == 3 for _, c in m.conditions["LineCondition2D3N"].values())
    assert m.validate() == []
    # shared diagonal (1, 3, 9) is not a boundary
    assert all(9 not in c for _, c in m.conditions["LineCondition2D3N"].values()
               if set(c) != {1, 3, 9})
    out = me.convert_cells(m, "quadratic_to_linear")
    assert out["elements"] == 2 and list(m.elements) == ["Element2D3N"]
    assert all(len(c) == 3 for _, c in m.elements["Element2D3N"].values())
    assert m.validate() == []
    assert total(m) == pytest.approx(1.0)


def test_quadratic_extrude_tri6_quad8_line3():
    e = me.extrude(tri6_mesh(), [0.5], cap_condition_names={"6": "SurfaceCondition3D6N"})
    assert list(e.elements) == ["Element3D15N"] and len(e.elements["Element3D15N"]) == 2
    assert e.validate() == []
    vols = me.quality(e)["elements"]["Element3D15N"]["measure"]
    assert vols["total"] == pytest.approx(0.5)
    assert len(e.sub_model_parts["zmax"].conditions) == 2

    m = mdpa_mod.Mdpa()
    m.nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (1, 1, 0), 4: (0, 1, 0),
               5: (0.5, 0, 0), 6: (1, 0.5, 0), 7: (0.5, 1, 0), 8: (0, 0.5, 0)}
    m.elements["Element2D8N"] = {1: (1, [1, 2, 3, 4, 5, 6, 7, 8])}
    m.conditions["LineCondition2D3N"] = {1: (1, [1, 2, 5])}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m.nodes), elements=[1], conditions=[1])
    e = me.extrude(m, [0.5])
    assert list(e.elements) == ["Element3D20N"]
    assert list(e.conditions) == ["LineCondition3D8N"]
    assert e.validate() == []
    assert me.quality(e)["elements"]["Element3D20N"]["measure"]["total"] == pytest.approx(0.5)

    q9 = mdpa_mod.Mdpa(nodes=dict(m.nodes), properties=dict(m.properties))
    q9.nodes[9] = (0.5, 0.5, 0.0)
    q9.elements["Element2D9N"] = {1: (1, [1, 2, 3, 4, 5, 6, 7, 8, 9])}
    with pytest.raises(ValueError, match="quadratic_to_linear"):
        me.extrude(q9, [0.5])


def test_quadratic_boundary_3d():
    # single tet10: volume 1/6, four 6-node faces
    m = mdpa_mod.Mdpa()
    m.nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (0, 0, 1),
               5: (0.5, 0, 0), 6: (0.5, 0.5, 0), 7: (0, 0.5, 0),
               8: (0, 0, 0.5), 9: (0.5, 0, 0.5), 10: (0, 0.5, 0.5)}
    m.elements["Element3D10N"] = {1: (1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m.nodes), elements=[1])
    assert me.measure(m, "Element3D10N", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == \
        pytest.approx(1 / 6)
    out = me.extract_boundary(m, {"6": "SurfaceCondition3D6N"}, "skin")
    assert out["conditions_created"] == 4 and m.validate() == []
    assert all(len(c) == 6 for _, (_, c) in m.conditions["SurfaceCondition3D6N"].items())
    # single hex20: volume 1, six 8-node faces
    m = mdpa_mod.Mdpa()
    corners = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
               (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
             (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    for i, p in enumerate(corners + [
            ((corners[a][0] + corners[b][0]) / 2, (corners[a][1] + corners[b][1]) / 2,
             (corners[a][2] + corners[b][2]) / 2) for a, b in edges]):
        m.nodes[i + 1] = (float(p[0]), float(p[1]), float(p[2]))
    m.elements["Element3D20N"] = {1: (1, list(range(1, 21)))}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m.nodes), elements=[1])
    assert me.measure(m, "Element3D20N", list(range(1, 21))) == pytest.approx(1.0)
    out = me.extract_boundary(m, {"8": "SurfaceCondition3D8N"}, "skin")
    assert out["conditions_created"] == 6 and m.validate() == []
    # hex27 faces carry the face center (bottom: corners + mids + node 21)
    m2 = mdpa_mod.Mdpa(nodes=dict(m.nodes))
    for i, p in enumerate([(0.5, 0.5, 0), (0.5, 0.5, 1), (0.5, 0, 0.5), (1, 0.5, 0.5),
                           (0.5, 1, 0.5), (0, 0.5, 0.5), (0.5, 0.5, 0.5)]):
        m2.nodes[21 + i] = p
    m2.elements["Element3D27N"] = {1: (1, list(range(1, 28)))}
    m2.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m2.nodes), elements=[1])
    out = me.extract_boundary(m2, {"9": "SurfaceCondition3D9N"}, "skin")
    assert out["conditions_created"] == 6 and m2.validate() == []
    bottom = [c for _, (_, c) in m2.conditions["SurfaceCondition3D9N"].items()
              if set(c[:4]) == {1, 2, 3, 4}]
    assert bottom == [[1, 4, 3, 2, 12, 11, 10, 9, 21]]


def test_split_modes_refuse_quadratic():
    m = mdpa_mod.Mdpa(nodes={i: (float(i % 3), float(i // 3), 0.0) for i in range(1, 9)})
    m.elements["Element2D8N"] = {1: (1, list(range(1, 9)))}
    with pytest.raises(ValueError, match="quadratic_to_linear"):
        me.convert_cells(m, "quad_to_tri")
    m = mdpa_mod.Mdpa(nodes={i: (float(i), 0, 0) for i in range(1, 21)})
    m.elements["Element3D20N"] = {1: (1, list(range(1, 21)))}
    with pytest.raises(ValueError, match="quadratic_to_linear"):
        me.convert_cells(m, "hex_to_tet")


def test_quadratic_mirror_keeps_positive_measure():
    m = tri6_mesh()
    original = {e: list(c) for e, (_, c) in m.elements["Element2D6N"].items()}
    me.transform(m, "mirror", point=[0, 0, 0], normal=[1, 0, 0])
    assert min(me.measure(m, t, c) for t, by in m.elements.items()
               for _, c in by.values()) > 0
    assert m.validate() == []
    # flip twice restores the connectivity
    me.transform(m, "mirror", point=[0, 0, 0], normal=[1, 0, 0])
    assert {e: list(c) for e, (_, c) in m.elements["Element2D6N"].items()} == original
    assert me.quality(m)["elements"]["Element2D6N"]["measure"]["total"] == pytest.approx(1.0)


# ------------------------------------------------- cubic cells and pyramids

def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def tri10_mesh():
    m = mdpa_mod.Mdpa()
    corners = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    pts = list(corners)
    for a, b in [(0, 1), (1, 2), (2, 0)]:
        pts += [_lerp(corners[a], corners[b], 1 / 3), _lerp(corners[a], corners[b], 2 / 3)]
    pts.append((1 / 3, 1 / 3, 0))
    m.nodes = {i + 1: p for i, p in enumerate(pts)}
    m.elements["Element2D10N"] = {1: (1, list(range(1, 11)))}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart(
        "domain", nodes=list(m.nodes), elements=[1])
    return m


def quad16_mesh():
    m = mdpa_mod.Mdpa()
    c = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    pts = list(c)
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 0)]:
        pts += [_lerp(c[a], c[b], 1 / 3), _lerp(c[a], c[b], 2 / 3)]
    pts += [(1 / 3, 1 / 3, 0), (2 / 3, 1 / 3, 0), (2 / 3, 2 / 3, 0), (1 / 3, 2 / 3, 0)]
    m.nodes = {i + 1: p for i, p in enumerate(pts)}
    m.elements["Element2D16N"] = {1: (1, list(range(1, 17)))}
    return m


def pyramid_mesh(n_nodes):
    m = mdpa_mod.Mdpa()
    base = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0.5, 1)]
    pts = list(base)
    if n_nodes >= 13:
        for a, b in [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)]:
            pts.append(_lerp(base[a], base[b], 0.5))
    if n_nodes == 14:
        pts.append((0.5, 0.5, 0))
    m.nodes = {i + 1: p for i, p in enumerate(pts)}
    m.elements[f"Element3D{n_nodes}N"] = {1: (1, list(range(1, n_nodes + 1)))}
    return m


def test_cubic_triangle_kind_corners_quality():
    m = tri10_mesh()
    conn = m.elements["Element2D10N"][1][1]
    assert m.validate() == []
    assert me.cell_kind(m, "Element2D10N", conn) == "tri"
    assert me.corner_nodes(m, "Element2D10N", conn) == [1, 2, 3]
    assert me.is_higher_order(m, "Element2D10N", conn)
    q = me.quality(m)["elements"]["Element2D10N"]
    assert q["measure"]["total"] == pytest.approx(0.5)
    # a 10-node 3D name stays a quadratic tetrahedron
    assert me.cell_kind(m, "Element3D10N", conn) == "tet"


def test_cubic_triangle_boundary_has_four_node_edges():
    m = tri10_mesh()
    out = me.extract_boundary(m, {"4": "LineCondition2D4N"}, "skin")
    assert out["conditions_created"] == 3
    edges = sorted(c for _, c in m.conditions["LineCondition2D4N"].values())
    # corners a, b then the two edge nodes in the a -> b direction
    assert edges == [[1, 2, 4, 5], [2, 3, 6, 7], [3, 1, 8, 9]]
    assert me.cell_kind(m, "LineCondition2D4N", edges[0]) == "line"
    assert m.validate() == []


def test_cubic_mirror_reorders_edge_node_pairs():
    m = tri10_mesh()
    me.transform(m, "scale", factors=[-1.0, 1.0, 1.0])
    conn = m.elements["Element2D10N"][1][1]
    assert me.measure(m, "Element2D10N", conn) > 0
    # edge nodes stay geometrically on their edge, ordered along the new direction
    x = {n: m.nodes[n] for n in conn}
    c0, c1, c2 = (np.array(x[i]) for i in conn[:3])
    for k, (a, b) in enumerate([(c0, c1), (c1, c2), (c2, c0)]):
        first, second = np.array(x[conn[3 + 2 * k]]), np.array(x[conn[4 + 2 * k]])
        assert np.allclose(first, a + (b - a) / 3) and np.allclose(second, a + 2 * (b - a) / 3)
    assert np.allclose(x[conn[9]], (c0 + c1 + c2) / 3)


def test_quad16_mirror_and_linearize():
    m = quad16_mesh()
    conn = m.elements["Element2D16N"][1][1]
    assert me.cell_kind(m, "Element2D16N", conn) == "quad"
    assert me.measure(m, "Element2D16N", conn) == pytest.approx(1.0)
    me.transform(m, "scale", factors=[1.0, -1.0, 1.0])
    conn = m.elements["Element2D16N"][1][1]
    assert me.measure(m, "Element2D16N", conn) > 0
    c = [np.array(m.nodes[i]) for i in conn[:4]]
    # interior node 12 + k stays next to corner k
    for k in range(4):
        assert np.linalg.norm(np.array(m.nodes[conn[12 + k]]) - c[k]) < 0.5
    me.convert_cells(m, "quadratic_to_linear")
    assert list(m.elements) == ["Element2D4N"] and len(m.elements["Element2D4N"][1][1]) == 4


@pytest.mark.parametrize("n_nodes", [5, 13, 14])
def test_pyramids(n_nodes):
    m = pyramid_mesh(n_nodes)
    name = f"Element3D{n_nodes}N"
    conn = m.elements[name][1][1]
    assert m.validate() == []
    assert me.cell_kind(m, name, conn) == "pyr"
    assert me.measure(m, name, conn) == pytest.approx(1 / 3)
    assert len(me.edge_lengths(m, name, conn)) == 8
    q = me.quality(m)["elements"][name]
    assert "skipped_unsupported" not in q and "non_positive_or_invalid" not in q
    me.transform(m, "scale", factors=[1.0, 1.0, -1.0])
    flipped = m.elements[name][1][1]
    assert me.measure(m, name, flipped) > 0
    assert sorted(flipped) == list(range(1, n_nodes + 1))


def test_pyramid_boundary_mixes_triangle_and_quad_faces():
    m = pyramid_mesh(5)
    me.extract_boundary(m, {"3": "T3", "4": "Q4"}, "skin")
    assert len(m.conditions["T3"]) == 4 and len(m.conditions["Q4"]) == 1
    m = pyramid_mesh(14)
    me.extract_boundary(m, {"6": "T6", "9": "Q9"}, "skin")
    assert len(m.conditions["T6"]) == 4
    assert list(m.conditions["Q9"].values())[0][1][-1] == 14  # base face centre
    me.convert_cells(m, "quadratic_to_linear")
    assert len(m.elements["Element3D5N"][1][1]) == 5


def test_cubic_cells_are_not_extruded_or_split():
    with pytest.raises(ValueError, match="quadratic_to_linear"):
        me.extrude(tri10_mesh(), [1.0])
    with pytest.raises(ValueError, match="quadratic_to_linear"):
        me.extrude(quad16_mesh(), [1.0])
