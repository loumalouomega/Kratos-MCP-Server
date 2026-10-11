from __future__ import annotations

import pytest

from kratos_mcp import mdpa


def test_rectangle_roundtrip(tmp_path):
    m = mdpa.create_rectangle_mesh(2.0, 1.0, 4, 2)
    path = m.write(tmp_path / "mesh.mdpa")
    m2 = mdpa.read(path)
    assert m2.inspect() == m.inspect()
    assert m2.validate() == []
    info = m2.inspect()
    assert info["num_nodes"] == 15
    assert info["num_elements"] == 8
    assert info["bounding_box"] == {"min": [0.0, 0.0, 0.0], "max": [2.0, 1.0, 0.0]}
    assert set(info["sub_model_parts"]) == {"domain", "left", "right", "top", "bottom"}
    assert info["sub_model_parts"]["left"]["nodes"] == 3
    assert info["sub_model_parts"]["left"]["conditions"] == 2


def test_rectangle_triangles():
    m = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2, triangles=True,
                                   element_name="Element2D3N")
    assert m.inspect()["num_elements"] == 8
    assert all(len(conn) == 3 for _, conn in m.elements["Element2D3N"].values())


def test_line_mesh():
    m = mdpa.create_line_mesh(3.0, 3)
    assert len(m.nodes) == 4
    assert m.nodes[4] == (3.0, 0.0, 0.0)
    assert m.sub_model_parts["end"].nodes == [4]


def test_box_mesh():
    m = mdpa.create_box_mesh(1, 2, 3, 2, 2, 2)
    info = m.inspect()
    assert info["num_nodes"] == 27
    assert info["num_elements"] == 8
    assert info["num_conditions"] == 24  # 4 quads per face * 6 faces
    assert info["bounding_box"]["max"] == [1.0, 2.0, 3.0]
    assert set(info["sub_model_parts"]) >= {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
    # hex connectivity is 8 unique nodes each
    for _, conn in m.elements["SmallDisplacementElement3D8N"].values():
        assert len(set(conn)) == 8


def test_validate_detects_dangling_refs():
    m = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2)
    m.sub_model_parts["left"].nodes.append(999)
    issues = m.validate()
    assert any("missing node 999" in i for i in issues)


def test_validate_detects_empty_submodelpart():
    m = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2)
    m.sub_model_parts["ghost"] = mdpa.SubModelPart("ghost")
    assert any("ghost is empty" in i for i in m.validate())


def test_parse_comments_and_nested_submodelparts():
    text = """
Begin ModelPartData
End ModelPartData
Begin Properties 1
End Properties
Begin Nodes // comment
    1 0.0 0.0 0.0
    2 1.0 0.0 0.0
End Nodes
Begin Elements Element2D2N
End Elements
Begin SubModelPart outer
    Begin SubModelPartNodes
        1
    End SubModelPartNodes
    Begin SubModelPart inner
        Begin SubModelPartNodes
            2
        End SubModelPartNodes
        Begin SubModelPartElements
        End SubModelPartElements
        Begin SubModelPartConditions
        End SubModelPartConditions
    End SubModelPart
End SubModelPart
"""
    m = mdpa.parse(text)
    assert m.sub_model_parts["outer"].nodes == [1]
    assert m.sub_model_parts["outer"].children["inner"].nodes == [2]


def test_parse_skips_unknown_blocks():
    text = """
Begin Nodes
    1 0 0 0
End Nodes
Begin Geometries Triangle2D3
    1 1 1 1
End Geometries
"""
    m = mdpa.parse(text)
    assert len(m.nodes) == 1


def test_unterminated_block_raises():
    with pytest.raises(ValueError):
        mdpa.parse("Begin Table 1\n1 2\n")


def test_data_blocks_round_trip():
    text = """
Begin ModelPartData
End ModelPartData
Begin Properties 1
End Properties
Begin Table 0 TIME VELOCITY
    0.0  0.0
    1.0  2.5
End Table
Begin Nodes
    1 0.0 0.0 0.0
    2 1.0 0.0 0.0
    3 1.0 1.0 0.0
End Nodes
Begin NodalData DISPLACEMENT_X
    1 1 0.0
    2 0 0.5
End NodalData
Begin NodalData DISPLACEMENT
    1 0 [3](1.0, 0.0, 0.0)
    2 1 [3](0.0, 2.0, 0.0)
End NodalData
Begin NodalData ACTIVE
    1
    2
End NodalData
Begin Elements SmallDisplacementElement2D3N
    1 1 1 2 3
End Elements
Begin Conditions LineLoadCondition2D2N
    1 1 1 2
End Conditions
Begin ElementalData DENSITY
    1 7850.0
End ElementalData
Begin ElementalData LOCAL_AXIS_2
    1 [3](0.0, 1.0, 0.0)
End ElementalData
Begin ConditionalData POINT_LOAD
    1 [3](0.0, -1.0, 0.0)
End ConditionalData
Begin SubModelPart domain
    Begin SubModelPartData
        TEMPERATURE 293.15
    End SubModelPartData
    Begin SubModelPartTables
        0
    End SubModelPartTables
    Begin SubModelPartNodes
        1 2 3
    End SubModelPartNodes
    Begin SubModelPartElements
        1
    End SubModelPartElements
    Begin SubModelPartConditions
        1
    End SubModelPartConditions
End SubModelPart
"""
    m = mdpa.parse(text)
    assert m.skipped_blocks == []
    assert m.tables[0] == mdpa.Table(0, "TIME", "VELOCITY", [(0.0, 0.0), (1.0, 2.5)])
    assert m.nodal_data == {"DISPLACEMENT_X": {1: 0.0, 2: 0.5}}
    assert m.nodal_data_fixed == {"DISPLACEMENT_X": {1}}
    assert m.vector_nodal_data == {"DISPLACEMENT": {1: (False, "[3](1.0, 0.0, 0.0)"),
                                                   2: (True, "[3](0.0, 2.0, 0.0)")}}
    assert m.nodal_flags == {"ACTIVE": [1, 2]}
    assert m.elemental_data == {"DENSITY": {1: "7850.0"},
                                "LOCAL_AXIS_2": {1: "[3](0.0, 1.0, 0.0)"}}
    assert m.conditional_data == {"POINT_LOAD": {1: "[3](0.0, -1.0, 0.0)"}}
    assert m.sub_model_parts["domain"].data == {"TEMPERATURE": "293.15"}
    assert m.sub_model_parts["domain"].tables == [0]
    assert m.validate() == []
    m2 = mdpa.parse(m.dumps())
    assert m2.tables == m.tables
    assert m2.nodal_data == m.nodal_data and m2.nodal_data_fixed == m.nodal_data_fixed
    assert m2.vector_nodal_data == m.vector_nodal_data and m2.nodal_flags == m.nodal_flags
    assert m2.elemental_data == m.elemental_data
    assert m2.conditional_data == m.conditional_data
    assert m2.sub_model_parts["domain"].data == {"TEMPERATURE": "293.15"}
    assert m2.sub_model_parts["domain"].tables == [0]
    assert m2.inspect() == m.inspect()


def test_data_blocks_validate_dangling_refs():
    m = mdpa.create_rectangle_mesh(1.0, 1.0, 1, 1)
    m.elemental_data = {"DENSITY": {999: "1.0"}}
    m.conditional_data = {"X": {999: "1.0"}}
    m.vector_nodal_data = {"DISPLACEMENT": {999: (False, "[3](0, 0, 0)")}}
    m.nodal_flags = {"ACTIVE": [999]}
    m.sub_model_parts["left"].tables.append(7)
    issues = m.validate()
    assert any("ElementalData DENSITY references missing element 999" in i for i in issues)
    assert any("ConditionalData X references missing condition 999" in i for i in issues)
    assert any("NodalData DISPLACEMENT references missing node 999" in i for i in issues)
    assert any("NodalData ACTIVE references missing node 999" in i for i in issues)
    assert any("references missing table 7" in i for i in issues)
