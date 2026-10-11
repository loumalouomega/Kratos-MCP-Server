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


def test_from_meshio_rejects_second_order_and_empty():
    import meshio

    pts = np.array([[0, 0], [1, 0], [0, 1], [0.5, 0], [0.5, 0.5], [0, 0.5]], dtype=float)
    mesh = meshio.Mesh(pts, [("triangle6", np.array([[0, 1, 2, 3, 4, 5]]))])
    with pytest.raises(ValueError, match="Unsupported"):
        mesh_io.from_meshio(mesh)
    with pytest.raises(ValueError, match="no line"):
        mesh_io.from_meshio(meshio.Mesh(pts, [("vertex", np.array([[0], [1]]))]))


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
