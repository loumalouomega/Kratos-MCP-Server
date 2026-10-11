"""Tool-layer tests for tools/mesh_edit.py, tools/geometry.py and tools/project.py."""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from kratos_mcp import mdpa as mdpa_mod, mesh_edit as me
from kratos_mcp.tools import geometry, mesh_edit as mesh_tools, project


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def collect(*modules):
    fake = FakeMCP()
    for m in modules:
        m.register(fake)

    class Tools:
        def __getattr__(self, name):
            fn = fake.tools[name]
            if inspect.iscoroutinefunction(fn):
                return lambda *a, **k: asyncio.run(fn(*a, **k))
            return fn

    return Tools(), fake.tools


T, TOOLS = collect(mesh_tools, geometry, project)


@pytest.fixture
def mesh_file(tmp_path):
    path = tmp_path / "plate.mdpa"
    mdpa_mod.create_rectangle_mesh(4.0, 2.0, 4, 2).write(path)
    return str(path)


def test_tool_inventory_and_schemas():
    from kratos_mcp.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    expected = {"mdpa_get_entities", "mdpa_select_nodes", "mdpa_add_nodes", "mdpa_move_nodes",
                "mdpa_delete_nodes", "mdpa_add_entities", "mdpa_delete_entities",
                "mdpa_set_entity_properties", "mdpa_create_sub_model_part",
                "mdpa_edit_sub_model_part", "mdpa_transform", "mdpa_merge", "mdpa_renumber",
                "mdpa_extract", "mdpa_extrude", "mdpa_convert_cells", "mdpa_extract_boundary",
                "mdpa_set_properties", "mdpa_set_nodal_data", "mdpa_quality",
                "mdpa_get_data_blocks", "mdpa_set_elemental_data", "mdpa_set_table",
                "mdpa_delete_table", "mdpa_set_sub_model_part_data",
                "mdpa_create_geometry_mesh", "mesh_import", "mesh_export", "mesh_generate_gmsh",
                "project_get_setting", "project_set_setting", "project_delete_setting",
                "project_list_processes", "project_add_process", "project_update_process",
                "project_remove_process", "project_update_solver", "materials_get",
                "materials_set_property", "materials_remove_property", "materials_check",
                "kratos_get_variable_info"}
    assert expected <= names


# ----------------------------------------------------------------- mesh tools

def test_in_place_and_output_semantics(mesh_file, tmp_path):
    out = T.mdpa_add_nodes(mesh_file, [[9, 9]])
    assert out["in_place"] and out["result"]["node_ids"] == [16]
    assert mdpa_mod.read(mesh_file).nodes[16] == (9.0, 9.0, 0.0)
    copy = str(tmp_path / "copy.mdpa")
    out = T.mdpa_move_nodes(mesh_file, node_ids=[16], offset=[1, 0, 0], output=copy)
    assert not out["in_place"] and mdpa_mod.read(copy).nodes[16] == (10.0, 9.0, 0.0)
    assert mdpa_mod.read(mesh_file).nodes[16] == (9.0, 9.0, 0.0)
    assert "already exists" in T.mdpa_move_nodes(mesh_file, node_ids=[16], offset=[1, 0, 0],
                                                  output=copy)["error"]
    assert "error" not in T.mdpa_move_nodes(mesh_file, node_ids=[16], offset=[1, 0, 0],
                                            output=copy, overwrite=True)
    assert "does not exist" in T.mdpa_add_nodes(str(tmp_path / "no.mdpa"), [[0, 0]])["error"]
    assert "exactly one" in T.mdpa_move_nodes(mesh_file, offset=[1, 0, 0])["error"]


def test_data_blocks_preserved_and_editable_in_place(tmp_path):
    path = tmp_path / "t.mdpa"
    mdpa_mod.create_rectangle_mesh(1, 1, 1, 1).write(path)
    path.write_text(path.read_text() + "\nBegin ElementalData DENSITY\n1 7850.0\nEnd ElementalData\n"
                    "Begin Table 0 TIME VELOCITY\n0.0 0.0\n1.0 2.0\nEnd Table\n")
    mesh = mdpa_mod.read(path)
    assert mesh.skipped_blocks == []
    assert mesh.elemental_data == {"DENSITY": {1: "7850.0"}}
    out = T.mdpa_add_nodes(str(path), [[5, 5]])
    assert out["in_place"] and "warnings" not in out
    again = mdpa_mod.read(path)
    assert again.elemental_data == {"DENSITY": {1: "7850.0"}}
    assert again.tables[0].rows == [(0.0, 0.0), (1.0, 2.0)]


def test_unknown_blocks_still_block_in_place_edit(tmp_path):
    path = tmp_path / "t.mdpa"
    mdpa_mod.create_rectangle_mesh(1, 1, 1, 1).write(path)
    path.write_text(path.read_text() + "\nBegin Constraints Foo\n1 2\nEnd Constraints\n")
    assert mdpa_mod.read(path).skipped_blocks == ["Constraints Foo"]
    err = T.mdpa_add_nodes(str(path), [[5, 5]])["error"]
    assert "Constraints" in err and "output" in err
    out = T.mdpa_add_nodes(str(path), [[5, 5]], output=str(tmp_path / "ok.mdpa"))
    assert out["warnings"] and "Constraints" in out["warnings"][0]


def test_select_create_part_and_loads(mesh_file):
    sel = T.mdpa_select_nodes(mesh_file, {"plane": {"point": [4, 0, 0], "normal": [1, 0, 0]}})
    assert sel["count"] == 3 and sel["bounding_box"]["min"][0] == 4.0
    out = T.mdpa_create_sub_model_part(
        mesh_file, "tip", selector={"plane": {"point": [4, 0, 0], "normal": [1, 0, 0]}},
        include=["condition"], exist_ok=False)
    assert out["valid"] and out["result"]["conditions"] == 2
    again = T.mdpa_create_sub_model_part(mesh_file, "tip", node_ids=[1])
    assert "already exists" in again["error"]
    renamed = T.mdpa_edit_sub_model_part(mesh_file, "tip", "rename", new_name="end")
    assert renamed["result"]["renamed_to"] == "end"
    deleted = T.mdpa_edit_sub_model_part(mesh_file, "end", "delete")
    assert "end" not in deleted["mesh"]["sub_model_parts"]
    assert "action" in T.mdpa_edit_sub_model_part(mesh_file, "left", "explode")["error"]


def test_entities_roundtrip(mesh_file):
    got = T.mdpa_get_entities(mesh_file, "element", sub_model_part="domain", limit=3)
    assert got["count"] == 8 and got["truncated"] and got["entities"][0]["nodes"] == [1, 2, 7, 6]
    added = T.mdpa_add_entities(mesh_file, "geometry", "Line2D2", [{"nodes": [1, 2]}])
    assert added["result"]["ids"] == [1]
    assert T.mdpa_get_entities(mesh_file, "geometry")["entities"] == [
        {"id": 1, "type": "Line2D2", "nodes": [1, 2]}]
    bad = T.mdpa_add_entities(mesh_file, "element", "SmallDisplacementElement2D4N",
                              [{"nodes": [1, 2, 3]}])
    assert "needs 4" in bad["error"]
    out = T.mdpa_set_entity_properties(mesh_file, "element", sub_model_part="domain",
                                       property_id=2)
    assert out["result"]["changed"] == 8 and out["mesh"]["properties_ids"] == [1, 2]
    out = T.mdpa_delete_entities(mesh_file, "condition", sub_model_part="top")
    assert out["result"]["deleted"] == 4 and out["valid"]
    assert "exactly one" in T.mdpa_delete_entities(mesh_file, "element")["error"]


def test_delete_nodes_cascade(mesh_file):
    assert "cascade" in T.mdpa_delete_nodes(mesh_file, node_ids=[1])["error"]
    out = T.mdpa_delete_nodes(mesh_file, node_ids=[1], cascade=True)
    assert out["valid"] and 1 not in mdpa_mod.read(mesh_file).nodes


def test_transform_merge_renumber_extract_extrude_convert(mesh_file, tmp_path):
    other = str(tmp_path / "other.mdpa")
    T.mdpa_transform(mesh_file, "translate", vector=[4, 0, 0], output=other)
    merged = str(tmp_path / "merged.mdpa")
    out = T.mdpa_merge(mesh_file, other, merged, merge_tolerance=1e-9)
    assert out["valid"] and out["result"]["merge"]["merged"] == 3
    assert out["mesh"]["num_elements"] == 16 and out["mesh"]["bounding_box"]["max"][0] == 8.0
    assert "output is required" in T.mdpa_merge(mesh_file, other, mesh_file)["error"]
    ren = T.mdpa_renumber(merged, 100)
    assert ren["result"]["nodes"]["first"] == 100
    part = str(tmp_path / "right.mdpa")
    ex = T.mdpa_extract(merged, "right", part)
    assert ex["mesh"]["num_nodes"] == 6  # both blocks' "right" parts were united
    solid = str(tmp_path / "solid.mdpa")
    out = T.mdpa_extrude(mesh_file, solid, [0.5, 0.5],
                         condition_names={"LineLoadCondition2D2N": "SurfaceLoadCondition3D4N"})
    assert out["valid"] and out["mesh"]["elements_by_type"] == {"SmallDisplacementElement3D8N": 16}
    tets = str(tmp_path / "tets.mdpa")
    out = T.mdpa_convert_cells(solid, "hex_to_tet", output=tets)
    assert out["valid"] and out["mesh"]["elements_by_type"] == {"SmallDisplacementElement3D4N": 96}
    q = T.mdpa_quality(tets)
    assert q["ok"]
    assert "mode" in T.mdpa_convert_cells(mesh_file, "nope")["error"]


def test_extract_boundary_properties_nodal_data(mesh_file):
    out = T.mdpa_extract_boundary(mesh_file, "LineCondition2D2N", "skin")
    assert out["result"]["conditions_created"] == 12 and out["valid"]
    out = T.mdpa_set_properties(mesh_file, 1, {"DENSITY": 7850.0},
                                model_part_data={"VARIABLES": "DISPLACEMENT"})
    assert out["result"]["properties"][1] == {"DENSITY": "7850.0"}
    assert mdpa_mod.read(mesh_file).properties[1] == {"DENSITY": "7850.0"}
    out = T.mdpa_set_nodal_data(mesh_file, "TEMPERATURE", 20.0, selector={"all": True})
    assert out["result"]["set"] == 15
    assert mdpa_mod.read(mesh_file).nodal_data["TEMPERATURE"][3] == 20.0
    assert "give" in T.mdpa_set_properties(mesh_file)["error"]


# ----------------------------------------------------------------- geometry tools

def test_geometry_tools(tmp_path):
    path = str(tmp_path / "ring.mdpa")
    out = T.mdpa_create_geometry_mesh(path, "annulus", {"r_inner": 1, "r_outer": 2,
                                                         "n_radial": 2, "n_theta": 16})
    assert out["valid"] and not out["in_place"] and out["mesh"]["num_nodes"] == 48
    assert "already exists" in T.mdpa_create_geometry_mesh(
        path, "annulus", {"r_inner": 1, "r_outer": 2, "n_radial": 2, "n_theta": 16})["error"]
    assert "Unknown params" in T.mdpa_create_geometry_mesh(
        path, "annulus", {"bogus": 1}, overwrite=True)["error"]
    assert "Unknown kind" in T.mdpa_create_geometry_mesh(path, "torus", {}, overwrite=True)["error"]
    cyl = T.mdpa_create_geometry_mesh(str(tmp_path / "c.mdpa"), "cylinder",
                                      {"radius": 1, "height": 1, "n_radial": 2, "n_theta": 8,
                                       "n_z": 2})
    assert cyl["valid"] and "zmax" in cyl["mesh"]["sub_model_parts"]
    poly = T.mdpa_create_geometry_mesh(str(tmp_path / "p.mdpa"), "polygon",
                                       {"vertices": [[0, 0], [1, 0], [0, 1]], "refinements": 1})
    assert poly["valid"] and poly["mesh"]["num_elements"] == 4


def test_import_export_tools(mesh_file, tmp_path):
    vtk = str(tmp_path / "plate.vtk")
    assert T.mesh_export(mesh_file, vtk)["written_to"] == vtk
    assert "already exists" in T.mesh_export(mesh_file, vtk)["error"]
    out = T.mesh_import(vtk, str(tmp_path / "back.mdpa"),
                        element_name="SmallDisplacementElement2D4N",
                        condition_name="LineLoadCondition2D2N")
    assert out["valid"] and out["mesh"]["num_elements"] == 8
    assert "does not exist" in T.mesh_import(str(tmp_path / "x.msh"), "y.mdpa")["error"]


def test_data_block_tools(tmp_path):
    path = str(tmp_path / "d.mdpa")
    mdpa_mod.create_rectangle_mesh(1, 1, 1, 1).write(path)
    out = T.mdpa_set_elemental_data(path, "elemental", "DENSITY", {"1": 7850.0})
    assert out["result"] == {"updated": 1, "removed": 0}
    out = T.mdpa_set_elemental_data(path, "elemental", "AXIS", {"1": "[3](0, 1, 0)"})
    assert "error" not in out
    assert "Missing element" in T.mdpa_set_elemental_data(
        path, "elemental", "DENSITY", {"999": 1.0})["error"]
    assert "kind" in T.mdpa_set_elemental_data(path, "x", "D", {"1": 1.0})["error"]
    out = T.mdpa_set_nodal_data(path, "DISP", "[3](1.0, 0.0, 0.0)", node_ids=[1],
                                fixed=True)
    assert out["result"]["set"] == 1
    out = T.mdpa_set_nodal_data(path, "TEMP", 20.0, node_ids=[1, 2], fixed=True)
    assert mdpa_mod.read(path).nodal_data_fixed["TEMP"] == {1, 2}
    out = T.mdpa_set_table(path, 0, "TIME", "VELOCITY", [[0, 0], [1, 2.0]])
    assert out["result"] == {"table": 0, "rows": 2}
    out = T.mdpa_set_sub_model_part_data(path, "left", data={"FLAG": "on"}, tables=[0])
    assert out["result"]["tables"] == [0]
    assert "Missing tables" in T.mdpa_set_sub_model_part_data(
        path, "left", tables=[9])["error"]
    got = T.mdpa_get_data_blocks(path)
    assert got["tables"] == {0: {"x_variable": "TIME", "y_variable": "VELOCITY", "rows": 2}}
    assert got["elemental_data"] == {"DENSITY": 1, "AXIS": 1}
    assert got["vector_nodal_data"] == {"DISP": 1}
    assert got["sub_model_part_data"]["left"] == {"data": {"FLAG": "on"}, "tables": [0],
                                                  "properties": []}
    assert got["skipped_blocks"] == []
    assert "referenced" in T.mdpa_delete_table(path, 0)["error"]
    T.mdpa_set_sub_model_part_data(path, "left", tables=[])
    assert T.mdpa_delete_table(path, 0)["result"] == {"deleted": 0}
    assert "Missing table" in T.mdpa_delete_table(path, 0)["error"]


def test_probe_elemental_quadratic_survives_renumber_and_import(tmp_path):
    """Roadmap 2.3 probe: an ElementalData block and a quadratic triangle
    round-trip through mdpa_renumber and mesh_import without losing either."""
    src = str(tmp_path / "q.mdpa")
    m = mdpa_mod.Mdpa()
    m.nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (1, 1, 0), 4: (0, 1, 0),
               5: (0.5, 0, 0), 6: (1, 0.5, 0), 7: (0.5, 1, 0), 8: (0, 0.5, 0)}
    m.elements["SmallDisplacementElement2D6N"] = {1: (1, [1, 2, 4, 5, 6, 7])}
    m.conditions["LineCondition2D3N"] = {1: (1, [1, 2, 5])}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart("domain", nodes=list(m.nodes),
                                                       elements=[1], conditions=[1])
    me.set_elemental_data(m, "elemental", "DENSITY", {1: 7850.0})
    m.write(src)
    ren = str(tmp_path / "r.mdpa")
    out = T.mdpa_renumber(src, 10, output=ren)
    assert "error" not in out
    reread = mdpa_mod.read(ren)
    assert reread.elemental_data == {"DENSITY": {10: "7850.0"}}
    assert reread.elements["SmallDisplacementElement2D6N"][10][1] == [10, 11, 13, 14, 15, 16]
    vtk = T.mesh_export(ren, str(tmp_path / "q.vtk"))
    assert "written_to" in vtk
    back = T.mesh_import(vtk["written_to"], str(tmp_path / "b.mdpa"),
                         element_name="SmallDisplacementElement2D6N",
                         condition_name="LineCondition2D3N")
    assert back["valid"] and back["mesh"]["elements_by_type"] == \
        {"SmallDisplacementElement2D6N": 1}
    reimported = mdpa_mod.read(str(tmp_path / "b.mdpa"))
    assert reimported.elements["SmallDisplacementElement2D6N"][1] == (1, [1, 2, 4, 5, 6, 7])
    assert reimported.conditions["LineCondition2D3N"][1] == (1, [1, 2, 5])


def test_gmsh_tool_validation_and_unavailable(tmp_path, monkeypatch):
    assert "vertices" in T.mesh_generate_gmsh(str(tmp_path / "a.mdpa"), "polygon")["error"]
    assert "kind" in T.mesh_generate_gmsh(str(tmp_path / "a.mdpa"), "blob")["error"]
    assert "shape" in T.mesh_generate_gmsh(str(tmp_path / "a.mdpa"), "shape")["error"]
    from kratos_mcp import mesh_io

    def boom(*a, **k):
        raise mesh_io.GmshUnavailable("No module named 'gmsh'")

    monkeypatch.setattr(mesh_io, "gmsh_generate", boom)
    out = T.mesh_generate_gmsh(str(tmp_path / "a.mdpa"), "polygon",
                               vertices=[[0, 0], [1, 0], [0, 1]])
    assert "gmsh is not available" in out["error"]


def test_gmsh_tool_end_to_end(tmp_path):
    from kratos_mcp import mesh_io

    if not mesh_io.gmsh_available():
        pytest.skip("gmsh not installed")
    out = T.mesh_generate_gmsh(str(tmp_path / "hole.mdpa"), "polygon",
                               vertices=[[0, 0], [2, 0], [2, 1], [0, 1]],
                               holes=[[[0.5, 0.25], [1.0, 0.25], [1.0, 0.75], [0.5, 0.75]]],
                               size=0.2, element_name="SmallDisplacementElement2D3N",
                               condition_name="LineLoadCondition2D2N")
    assert out["valid"] and "hole_0" in out["mesh"]["sub_model_parts"]
    assert out["gmsh"]["dimension"] == 2


# --------------------------------------------------------------- project tools

@pytest.fixture
def case(tmp_path):
    from kratos_mcp.tools import scaffold

    fake = FakeMCP()
    scaffold.register(fake)
    d = tmp_path / "case"
    out = fake.tools["create_project"](str(d), "structural_static", create_demo_mesh=True)
    assert "error" not in out
    return d


def test_settings_get_set_delete(case):
    pf = str(case / "ProjectParameters.json")
    assert T.project_get_setting(pf, "/solver_settings/time_stepping/time_step")["value"] == 1.1
    out = T.project_set_setting(pf, "/solver_settings/time_stepping/time_step", 0.5)
    assert out["previous"] == 1.1
    assert json.loads((case / "ProjectParameters.json").read_text())["solver_settings"][
        "time_stepping"]["time_step"] == 0.5
    T.project_set_setting(pf, "/solver_settings/new/nested/key", [1, 2])
    assert T.project_get_setting(pf, "/solver_settings/new/nested/key")["value"] == [1, 2]
    assert "error" in T.project_set_setting(pf, "/solver_settings/x/y", 1, create=False)
    assert T.project_delete_setting(pf, "/solver_settings/new")["removed"] == {"nested": {"key": [1, 2]}}
    assert "no key" in T.project_get_setting(pf, "/solver_settings/new")["error"]
    assert "no 'stages'" in T.project_get_setting(pf, "", stage="a")["error"]
    assert "must start" in T.project_get_setting(pf, "solver_settings")["error"]


def test_process_crud(case):
    pf = str(case / "ProjectParameters.json")
    listed = T.project_list_processes(pf)
    assert listed["count"] >= 2
    out = T.project_add_process(
        pf, "assign_scalar_variable_process",
        {"model_part_name": "Structure.nope", "variable_name": "TEMPERATURE", "value": 5.0,
         "interval": [0.0, "End"]}, process_list="list_other_processes")
    assert out["index"] == 0 and out["added"]["kratos_module"] == "KratosMultiphysics"
    assert out["added"]["process_name"] == "AssignScalarVariableProcess"
    assert any("Structure.nope" in w for w in out["warnings"])
    out = T.project_add_process(pf, "my_custom_process", {"a": 1}, section="output_processes",
                                process_list="custom", position=0)
    assert any("defaulted" in w for w in out["warnings"])
    upd = T.project_update_process(pf, {"value": 9.0, "extra": {"k": 1}},
                                   python_module="assign_scalar_variable_process")
    assert upd["updated"]["Parameters"]["value"] == 9.0
    upd = T.project_update_process(pf, {"a": 2}, python_module="my_custom_process", replace=True)
    assert upd["updated"]["Parameters"] == {"a": 2}
    assert "No process" in T.project_update_process(pf, {}, python_module="zzz")["error"]
    dup = T.project_add_process(pf, "assign_scalar_variable_process",
                                {"model_part_name": "Structure.left", "variable_name": "TEMPERATURE"})
    ambiguous = T.project_remove_process(pf, python_module="assign_scalar_variable_process")
    assert "narrow" in ambiguous["error"]
    gone = T.project_remove_process(pf, python_module="assign_scalar_variable_process",
                                    all_matches=True)
    assert gone["removed_count"] == 2
    assert T.project_remove_process(pf, process_list="custom", index=0,
                                    section="output_processes")["removed_count"] == 1
    assert "python_module" in T.project_add_process(pf, "bad module")["error"]


def test_update_solver(case):
    pf = str(case / "ProjectParameters.json")
    out = T.project_update_solver(pf, settings={"echo_level": 2}, linear_solver_preset="cg",
                                  time_step=0.1, end_time=2.0, mesh_file="other.mdpa",
                                  materials_file="M2.json", domain_size=2)
    data = json.loads((case / "ProjectParameters.json").read_text())
    s = data["solver_settings"]
    assert s["echo_level"] == 2 and s["time_stepping"]["time_step"] == 0.1
    assert s["linear_solver_settings"]["solver_type"].endswith("cg")
    assert s["model_import_settings"]["input_filename"] == "other"
    assert s["material_import_settings"]["materials_filename"] == "M2.json"
    assert data["problem_data"]["end_time"] == 2.0 and "changed" in out
    assert "preset" in T.project_update_solver(pf, linear_solver_preset="zzz")["error"]
    assert "end_time" in T.project_update_solver(pf, end_time=-1.0)["error"]
    assert "nothing" in T.project_update_solver(pf)["error"]


def test_multistage_scope(tmp_path):
    from kratos_mcp.tools import scaffold

    fake = FakeMCP()
    scaffold.register(fake)
    d = tmp_path / "ms"
    out = fake.tools["create_multistage_project"](
        str(d), [{"name": "a", "template": "structural_static"},
                 {"name": "b", "template": "structural_static"}])
    assert "error" not in out
    pf = str(d / "ProjectParameters.json")
    assert "stage" in T.project_list_processes(pf)["error"]
    assert T.project_list_processes(pf, stage="a")["count"] >= 1
    T.project_set_setting(pf, "/solver_settings/echo_level", 3, stage="b")
    data = json.loads((d / "ProjectParameters.json").read_text())
    assert data["stages"]["b"]["stage_settings"]["solver_settings"]["echo_level"] == 3
    assert data["stages"]["a"]["stage_settings"]["solver_settings"]["echo_level"] != 3
    assert "Unknown stage" in T.project_set_setting(pf, "/x", 1, stage="zz")["error"]


def test_lenient_json_with_comments(tmp_path):
    p = tmp_path / "pp.json"
    p.write_text('{\n // a comment\n "a": "http://x // not a comment", "b": 1 // tail\n}\n')
    assert T.project_get_setting(str(p), "/a")["value"] == "http://x // not a comment"
    T.project_set_setting(str(p), "/b", 2)
    assert json.loads(p.read_text())["b"] == 2


def test_materials_tools(tmp_path):
    mf = str(tmp_path / "Materials.json")
    out = T.materials_set_property(mf, "Structure.domain",
                                   constitutive_law="LinearElasticPlaneStrain2DLaw",
                                   variables={"YOUNG_MODULUS": 2.1e11, "POISSON_RATIO": 0.3,
                                              "DENSITY": 7850.0})
    assert out["created"] and out["entry"]["properties_id"] == 1
    out = T.materials_set_property(mf, "Structure.domain", variables={"YOUNG_MODULUS": 1e9})
    assert not out["created"]
    assert out["entry"]["Material"]["Variables"]["YOUNG_MODULUS"] == 1e9
    assert out["entry"]["Material"]["Variables"]["POISSON_RATIO"] == 0.3
    second = T.materials_set_property(mf, "Structure.top", constitutive_law="X")
    assert second["entry"]["properties_id"] == 2
    got = T.materials_get(mf)
    assert got["count"] == 2 and got["entries"][0]["constitutive_law"] == "LinearElasticPlaneStrain2DLaw"
    assert T.materials_remove_property(mf, properties_id=2)["remaining"] == 1
    assert "No matching" in T.materials_remove_property(mf, "Nope")["error"]
    assert "Unknown preset" in T.materials_set_property(mf, "A", preset="zzz")["error"]
    from kratos_mcp.tools.scaffold import load_material_presets

    name, preset = next(iter(load_material_presets().items()))
    out = T.materials_set_property(mf, "Structure.preset", preset=name)
    assert out["entry"]["Material"]["Variables"] == preset.get("variables", {})


def test_materials_check(case):
    pf = str(case / "ProjectParameters.json")
    out = T.materials_check(pf)
    assert out["entries"] >= 1
    T.materials_set_property(str(case / "Materials.json"), "Structure.ghost",
                             constitutive_law="LinearElasticPlaneStrain2DLaw", properties_id=7)
    out = T.materials_check(pf)
    assert not out["valid"] and any("ghost" in i for i in out["issues"])
    T.materials_remove_property(str(case / "Materials.json"), properties_id=1)
    out = T.materials_check(pf)
    assert any("properties_id 1" in i for i in out["issues"])


def test_add_boundary_condition_generic_and_staged(case, tmp_path):
    from kratos_mcp.tools import scaffold

    fake = FakeMCP()
    scaffold.register(fake)
    add = fake.tools["add_boundary_condition"]
    pf = str(case / "ProjectParameters.json")
    out = add(pf, "fix_scalar", "Structure.left", value=2.0, variable="DISTANCE")
    block = out["added"]
    assert block["Parameters"]["variable_name"] == "DISTANCE"
    assert block["Parameters"]["constrained"] is True and block["Parameters"]["value"] == 2.0
    out = add(pf, "assign_vector", "Structure.top", value=[1.0, 0.0, 0.0], variable="VELOCITY")
    assert out["added"]["Parameters"]["constrained"] == [False] * 3
    assert "needs 'variable'" in add(pf, "fix_scalar", "Structure.left")["error"]
    # default variable is unchanged for the classic kinds
    assert add(pf, "fix_displacement", "Structure.left", value=[0, 0, 0])["added"][
        "Parameters"]["variable_name"] == "DISPLACEMENT"

    d = tmp_path / "ms2"
    fake.tools["create_multistage_project"](
        str(d), [{"name": "a", "template": "structural_static"},
                 {"name": "b", "template": "structural_static"}])
    pf2 = str(d / "ProjectParameters.json")
    assert "stage" in add(pf2, "fix_displacement", "Structure.left", value=[0, 0, 0])["error"]
    add(pf2, "point_load", "Structure.right", modulus=1.0, direction=[0, -1, 0], stage="b")
    data = json.loads((d / "ProjectParameters.json").read_text())
    loads_b = data["stages"]["b"]["stage_settings"]["processes"]["loads_process_list"]
    loads_a = data["stages"]["a"]["stage_settings"]["processes"].get("loads_process_list", [])
    assert len(loads_b) == len(loads_a) + 1
