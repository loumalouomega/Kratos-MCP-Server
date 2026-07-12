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
