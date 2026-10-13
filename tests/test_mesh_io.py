"""Unit tests for kratos_mcp.mesh_io: meshio conversion and the gmsh worker."""

from __future__ import annotations

import math
import subprocess
import sys

import numpy as np
import pytest

from kratos_mcp import mdpa as mdpa_mod, mesh_edit as me, mesh_io


def test_to_from_meshio_round_trip(tmp_path):
    m = mdpa_mod.create_rectangle_mesh(2.0, 1.0, 4, 2)
    me.set_nodal_data(m, "TEMPERATURE", [1, 2], 3.0)
    mio = mesh_io.to_meshio(m)
    assert {c.type for c in mio.cells} == {"quad", "line"}
    assert set(mio.cell_sets) >= {"domain", "left", "bottom"}
    back = mesh_io.from_meshio(mio, element_name="SmallDisplacementElement2D4N",
                               condition_name="LineLoadCondition2D2N")
    assert back.inspect()["num_nodes"] == m.inspect()["num_nodes"]
    assert back.inspect()["elements_by_type"] == {"SmallDisplacementElement2D4N": 8}
    assert back.inspect()["conditions_by_type"] == {"LineLoadCondition2D2N": 12}
    assert back.validate() == []
    for name in ("left", "bottom", "right", "top"):
        assert len(back.sub_model_parts[name].conditions) == len(m.sub_model_parts[name].conditions)
        assert sorted(back.sub_model_parts[name].nodes) == sorted(m.sub_model_parts[name].nodes)
    assert len(back.sub_model_parts["domain"].elements) == 8


def test_export_import_files(tmp_path):
    m = mdpa_mod.create_box_mesh(1, 1, 1, 2, 2, 2)
    for ext in (".vtk", ".vtu"):
        path = mesh_io.export_mesh(m, tmp_path / f"box{ext}")
        assert path.exists()
        back = mesh_io.import_mesh(path)
        assert back.inspect()["num_nodes"] == 27
        assert back.inspect()["elements_by_type"] == {"Element3D8N": 8}
        assert back.inspect()["conditions_by_type"] == {"SurfaceCondition3D4N": 24}
        assert back.validate() == []


def test_from_meshio_quadratic_and_rejections():
    import meshio

    pts = np.array([[0, 0], [1, 0], [0, 1], [0.5, 0], [0.5, 0.5], [0, 0.5]], dtype=float)
    m = mesh_io.from_meshio(meshio.Mesh(pts, [("triangle6", np.array([[0, 1, 2, 3, 4, 5]]))]))
    assert m.inspect()["elements_by_type"] == {"Element2D6N": 1}
    assert m.validate() == []
    with pytest.raises(ValueError, match="Unsupported"):
        mesh_io.from_meshio(meshio.Mesh(pts, [("triangle15", np.arange(15)[None] % 3)]))
    with pytest.raises(ValueError, match="no line"):
        mesh_io.from_meshio(meshio.Mesh(pts, [("vertex", np.array([[0], [1]]))]))


def test_quadratic_meshio_round_trips(tmp_path):
    import meshio

    # triangle6 + line3 through real files
    m = mdpa_mod.Mdpa()
    m.nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (1, 1, 0), 4: (0, 1, 0),
               5: (0.5, 0, 0), 6: (1, 0.5, 0), 7: (0.5, 1, 0), 8: (0, 0.5, 0)}
    m.elements["SmallDisplacementElement2D6N"] = {1: (1, [1, 2, 4, 5, 6, 7])}
    m.conditions["LineCondition2D3N"] = {1: (1, [1, 2, 5])}
    m.sub_model_parts["domain"] = mdpa_mod.SubModelPart("domain", nodes=list(m.nodes),
                                                       elements=[1], conditions=[1])
    me.set_nodal_data(m, "DISP", [1], "[3](1.0, 0.0, 0.0)")
    path = mesh_io.export_mesh(m, tmp_path / "q.vtk")
    back = mesh_io.import_mesh(path, element_name="SmallDisplacementElement2D6N",
                               condition_name="LineCondition2D3N")
    assert back.elements["SmallDisplacementElement2D6N"][1] == (1, [1, 2, 4, 5, 6, 7])
    assert back.conditions["LineCondition2D3N"][1] == (1, [1, 2, 5])
    assert back.validate() == []
    assert len(back.sub_model_parts["domain"].elements) == 1
    # each supported quadratic type round-trips in memory
    for cell_type, n in [("quad8", 8), ("quad9", 9), ("tetra10", 10),
                         ("hexahedron20", 20), ("hexahedron27", 27), ("line3", 3)]:
        pts = np.zeros((n, 3))
        pts[:, 0] = np.arange(n)
        src = meshio.Mesh(pts, [(cell_type, np.array([list(range(n))]))])
        got = mesh_io.from_meshio(src)
        assert got.validate() == [], cell_type
        assert [c.type for c in mesh_io.to_meshio(got).cells] == [cell_type], cell_type


def test_cubic_cells_round_trip_through_gmsh_msh(tmp_path):
    """Roadmap probe: a cubic (10-node) triangle survives mesh_import/export."""
    import meshio

    P = [(0, 0), (1, 0), (0, 1)]
    pts = list(P)
    for a, b in [(0, 1), (1, 2), (2, 0)]:
        for t in (1 / 3, 2 / 3):
            pts.append((P[a][0] + (P[b][0] - P[a][0]) * t, P[a][1] + (P[b][1] - P[a][1]) * t))
    pts.append((1 / 3, 1 / 3))
    src = meshio.Mesh(np.array(pts), [("triangle10", np.array([list(range(10))]))])
    m = mesh_io.from_meshio(src)
    assert m.inspect()["elements_by_type"] == {"Element2D10N": 1}
    assert me.cell_kind(m, "Element2D10N", m.elements["Element2D10N"][1][1]) == "tri"
    assert m.validate() == []
    path = mesh_io.export_mesh(m, tmp_path / "t10.msh")
    back = mesh_io.import_mesh(path)
    assert back.elements["Element2D10N"][1][1] == list(range(1, 11))
    # VTK has no triangle10: an explanatory error, no stray file
    with pytest.raises(ValueError, match="gmsh .msh"):
        mesh_io.export_mesh(m, tmp_path / "t10.vtk")
    assert not (tmp_path / "t10.vtk").exists()
    # boundary edges become 4-node line conditions that survive the round trip
    me.extract_boundary(m, {"4": "LineCondition2D4N"}, "skin")
    assert [c.type for c in mesh_io.to_meshio(m).cells] == ["triangle10", "line4"]
    again = mesh_io.from_meshio(mesh_io.to_meshio(m))
    assert again.inspect()["conditions_by_type"] == {"LineCondition2D4N": 3}


def test_quad16_and_pyramids_map_to_meshio():
    import meshio

    for cell_type, n in [("quad16", 16), ("pyramid", 5), ("pyramid14", 14), ("line4", 4)]:
        pts = np.random.default_rng(0).random((n, 3))
        src = meshio.Mesh(pts, [(cell_type, np.array([list(range(n))]))])
        got = mesh_io.from_meshio(src)
        assert got.validate() == [], cell_type
        assert [c.type for c in mesh_io.to_meshio(got).cells] == [cell_type], cell_type


def test_pyramid13_needs_linearize_flag():
    from types import SimpleNamespace

    fake = SimpleNamespace(cells=[SimpleNamespace(type="pyramid13", data=np.array([list(range(13))]))],
                           points=np.random.default_rng(1).random((13, 3)), cell_sets={},
                           cell_data={}, point_sets={})
    m = mesh_io.from_meshio(fake)
    with pytest.raises(ValueError, match="13-node pyramid"):
        mesh_io.to_meshio(m)
    notes: list[str] = []
    got = mesh_io.to_meshio(m, linearize_unsupported=True, warnings=notes)
    assert [c.type for c in got.cells] == ["pyramid"]
    assert got.cells[0].data.tolist() == [[0, 1, 2, 3, 4]]
    assert len(notes) == 1 and "corner" in notes[0]


def test_wedge15_import_export_limits():
    from types import SimpleNamespace

    # meshio 5.3 cannot construct wedge15 blocks itself, but from_meshio
    # handles them when present (e.g. from a fixed meshio or a hand-built mesh)
    cells = [SimpleNamespace(type="wedge15", data=np.array([list(range(15))]))]
    fake = SimpleNamespace(cells=cells, points=np.zeros((15, 3)), cell_sets={},
                           cell_data={}, point_sets={})
    m = mesh_io.from_meshio(fake)
    assert m.inspect()["elements_by_type"] == {"Element3D15N": 1}
    assert m.validate() == []
    with pytest.raises(ValueError, match="15-node wedge"):
        mesh_io.to_meshio(m)
    notes: list[str] = []
    linear = mesh_io.to_meshio(m, linearize_unsupported=True, warnings=notes)
    assert [c.type for c in linear.cells] == ["wedge"] and linear.cells[0].data.shape == (1, 6)
    assert notes


def test_vector_nodal_data_export(tmp_path):
    import meshio

    m = mdpa_mod.create_rectangle_mesh(1.0, 1.0, 1, 1)
    me.set_nodal_data(m, "DISP", [1, 2], "[3](1.0, 2.0, 3.0)")
    mio = mesh_io.to_meshio(m)
    assert mio.point_data["DISP"].shape == (len(m.nodes), 3)
    assert list(mio.point_data["DISP"][0]) == [1.0, 2.0, 3.0]
    # unparsable raw values are skipped, never break the export
    m.vector_nodal_data["ODD"] = {1: (False, "not-a-vector")}
    assert "ODD" not in mesh_io.to_meshio(m).point_data


def test_physical_tags_without_names():
    import meshio

    pts = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    mesh = meshio.Mesh(pts, [("triangle", np.array([[0, 1, 2], [0, 2, 3]])),
                             ("line", np.array([[0, 1]]))],
                       cell_data={"gmsh:physical": [np.array([7, 7]), np.array([9])]})
    m = mesh_io.from_meshio(mesh)
    assert set(m.sub_model_parts) == {"domain", "physical_7", "physical_9"}
    assert len(m.sub_model_parts["physical_7"].elements) == 2


gmsh_ok = subprocess.run([sys.executable, "-c", "import gmsh"], capture_output=True).returncode == 0
needs_gmsh = pytest.mark.skipif(not gmsh_ok, reason="gmsh is not installed (uv sync --extra gmsh)")


@needs_gmsh
def test_gmsh_polygon_with_hole():
    m, info = mesh_io.gmsh_generate({
        "kind": "polygon", "vertices": [[0, 0], [4, 0], [4, 2], [0, 2]],
        "holes": [[[1, 0.5], [2, 0.5], [2, 1.5], [1, 1.5]]], "size": 0.25},
        element_name="SmallDisplacementElement2D3N", condition_name="LineLoadCondition2D2N")
    assert m.validate() == []
    total = sum(me.measure(m, t, c) for t, by in m.elements.items() for _, c in by.values())
    assert total == pytest.approx(8.0 - 1.0, rel=1e-6)
    assert {"domain", "side_0", "side_3", "hole_0"} <= set(m.sub_model_parts)
    assert me.quality(m)["elements"]["SmallDisplacementElement2D3N"]["measure"]["min"] > 0
    assert info["dimension"] == 2


@needs_gmsh
def test_gmsh_quads_and_shapes():
    m, _ = mesh_io.gmsh_generate({"kind": "polygon", "vertices": [[0, 0], [1, 0], [1, 1], [0, 1]],
                                  "size": 0.25, "quads": True},
                                 element_name="SmallDisplacementElement2D4N")
    assert set(m.elements) == {"SmallDisplacementElement2D4N"}
    ball, info = mesh_io.gmsh_generate({"kind": "shape", "shape": "sphere", "radius": 1.0,
                                        "size": 0.3})
    vol = sum(me.measure(ball, t, c) for t, by in ball.elements.items() for _, c in by.values())
    assert vol == pytest.approx(4 / 3 * math.pi, rel=0.1)
    assert "boundary" in ball.sub_model_parts and info["dimension"] == 3
    with pytest.raises(RuntimeError, match="shape"):
        mesh_io.gmsh_generate({"kind": "shape", "shape": "torus"})
    with pytest.raises(RuntimeError, match="kind"):
        mesh_io.gmsh_generate({"kind": "nope"})


def test_gmsh_unavailable_is_reported(monkeypatch, tmp_path):
    """A worker that cannot import gmsh surfaces as GmshUnavailable."""
    real_run = subprocess.run

    def fake(cmd, *a, **k):
        if "kratos_mcp.gmsh_worker" in cmd:
            res = cmd[cmd.index("--result-file") + 1]
            import json
            open(res, "w").write(json.dumps({"ok": False,
                                             "error": "ModuleNotFoundError: No module named 'gmsh'"}))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(mesh_io.GmshUnavailable):
        mesh_io.gmsh_generate({"kind": "polygon", "vertices": [[0, 0], [1, 0], [0, 1]]})
