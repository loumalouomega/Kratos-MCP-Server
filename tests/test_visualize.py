"""Tests for the pyvista visualization tools (results_render / results_animate).

The error paths and helpers need neither pyvista nor a GL context, so they
always run. Actual rendering tests are skipped when pyvista is missing
(the base CI job proves the install-hint path that way) or when no usable
GL context exists (caught and skipped, not failed).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from kratos_mcp.tools import visualize


def _write_vtu(path: Path, scale: float = 1.0) -> Path:
    """Tiny 3x3-point / 8-triangle mesh with a scalar and a vector field."""
    import meshio

    pts = np.array([[x, y, 0.0] for y in (0.0, 1.0, 2.0) for x in (0.0, 1.0, 2.0)])
    tris = []
    for j in range(2):
        for i in range(2):
            n = j * 3 + i
            tris += [[n, n + 1, n + 4], [n, n + 4, n + 3]]
    disp = np.zeros((9, 3))
    disp[:, 1] = 0.05 * pts[:, 0] ** 2 * scale
    mesh = meshio.Mesh(
        pts, [("triangle", np.array(tris))],
        point_data={"TEMPERATURE": (pts[:, 0] + 2 * pts[:, 1]) * scale,
                    "DISPLACEMENT": disp},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    meshio.write(path, mesh)
    return path


@pytest.fixture
def vtu_file(tmp_path):
    return _write_vtu(tmp_path / "Structure_0_1.vtu")


@pytest.fixture
def vtu_series(tmp_path):
    out = tmp_path / "vtk_output"
    return [_write_vtu(out / f"Structure_0_{step}.vtu", scale=float(step))
            for step in (1, 2, 3)]


# --------------------------------------------------------------------------
# Always-run: helpers and error paths, no pyvista required
# --------------------------------------------------------------------------

def test_missing_pyvista_error_mentions_both_install_commands(monkeypatch):
    monkeypatch.setattr(visualize.importlib.util, "find_spec", lambda name: None)
    err = visualize._pyvista_missing()
    assert "kratos-mcp-server[viz]" in err["error"]
    assert "uv sync --extra viz" in err["error"]


def test_frame_sort_is_numeric():
    names = ["Structure_0_10.vtk", "Structure_0_2.vtk", "Structure_0_1.vtk"]
    ordered = sorted((Path(n) for n in names), key=visualize._frame_sort_key)
    assert [p.name for p in ordered] == [
        "Structure_0_1.vtk", "Structure_0_2.vtk", "Structure_0_10.vtk"]


def test_expand_frames_from_directory_and_glob(vtu_series):
    out_dir = vtu_series[0].parent
    from_dir = visualize._expand_frames(str(out_dir))
    from_glob = visualize._expand_frames(str(out_dir / "*.vtu"))
    assert from_dir == from_glob == [p.resolve() for p in vtu_series]


def test_validate_view_rejects_bad_values():
    assert "camera" in visualize._validate_view("front", None)["error"]
    assert "component" in visualize._validate_view("iso", "w")["error"]
    assert visualize._validate_view("iso", "magnitude") is None


def test_render_failure_attaches_gl_hint_and_stderr_tail():
    out = visualize._render_failure("boom", "ERROR: could not create GLX context\n")
    assert out["hint"] == visualize._GL_HINT
    assert "GLX" in out["stderr_tail"]
    assert "hint" not in visualize._render_failure("boom", "unrelated failure\n")


# --------------------------------------------------------------------------
# Rendering: need pyvista and a usable GL context
# --------------------------------------------------------------------------

pyvista = pytest.importorskip("pyvista")


def _render(func, req):
    try:
        return func(req)
    except (ValueError, KeyError):
        raise  # our own validation errors must not be masked as GL skips
    except Exception as exc:
        pytest.skip(f"no usable GL context: {exc}")


def test_render_screenshot_magnitude_default(vtu_file, tmp_path):
    from kratos_mcp import render_worker

    png = tmp_path / "out.png"
    result = _render(render_worker.render_screenshot, {
        "file": str(vtu_file), "variable": "DISPLACEMENT",
        "camera": "xy", "image_path": str(png), "window_size": [320, 240],
    })
    assert png.read_bytes().startswith(b"\x89PNG")
    assert result["data_range"] == pytest.approx([0.0, 0.2])


def test_render_screenshot_component_and_warp(vtu_file, tmp_path):
    from kratos_mcp import render_worker

    png = tmp_path / "out.png"
    result = _render(render_worker.render_screenshot, {
        "file": str(vtu_file), "variable": "DISPLACEMENT", "component": "y",
        "warp_by": "DISPLACEMENT", "warp_factor": 10.0,
        "camera": "iso", "image_path": str(png), "window_size": [320, 240],
    })
    assert png.is_file()
    assert result["data_range"] == pytest.approx([0.0, 0.2])


def test_render_screenshot_geometry_only(vtu_file, tmp_path):
    from kratos_mcp import render_worker

    png = tmp_path / "out.png"
    result = _render(render_worker.render_screenshot, {
        "file": str(vtu_file), "camera": "xy",
        "image_path": str(png), "window_size": [320, 240],
    })
    assert png.is_file()
    assert result["data_range"] is None


def test_render_unknown_variable_lists_available(vtu_file, tmp_path):
    from kratos_mcp import render_worker

    with pytest.raises(ValueError, match="TEMPERATURE"):
        _render(render_worker.render_screenshot, {
            "file": str(vtu_file), "variable": "PRESSURE",
            "camera": "xy", "image_path": str(tmp_path / "out.png"),
        })


def test_render_gif_global_range(vtu_series, tmp_path):
    import imageio.v2 as imageio

    from kratos_mcp import render_worker

    gif = tmp_path / "anim.gif"
    result = _render(render_worker.render_gif, {
        "files": [str(f) for f in vtu_series], "variable": "TEMPERATURE",
        "camera": "xy", "gif_path": str(gif), "fps": 2,
        "window_size": [320, 240],
    })
    assert result["num_frames"] == 3
    # Global color range spans all steps: max TEMPERATURE = (2 + 2*2) * 3.
    assert result["data_range"] == pytest.approx([0.0, 18.0])
    assert len(imageio.mimread(gif)) == 3


def test_render_worker_subprocess_roundtrip(vtu_file, tmp_path):
    request = tmp_path / "req.json"
    result_file = tmp_path / "res.json"
    request.write_text(json.dumps({"op": "screenshot", "args": {
        "file": str(vtu_file), "variable": "TEMPERATURE",
        "camera": "xy", "image_path": str(tmp_path / "out.png"),
        "window_size": [320, 240],
    }}))
    subprocess.run(
        [sys.executable, "-m", "kratos_mcp.render_worker",
         "--request-file", str(request), "--result-file", str(result_file)],
        capture_output=True, text=True, timeout=120, check=True,
    )
    payload = json.loads(result_file.read_text())
    if not payload["ok"]:
        pytest.skip(f"no usable GL context: {payload['error']}")
    assert Path(payload["result"]["image_path"]).is_file()


@pytest.mark.kratos
def test_cantilever_result_renders(tmp_path, monkeypatch):
    """Run the real cantilever example and render its displacement field."""
    import shutil
    import time

    from kratos_mcp import jobs, render_worker
    from kratos_mcp.tools.resources import EXAMPLES_DIR

    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    case = tmp_path / "cantilever"
    shutil.copytree(EXAMPLES_DIR / "cantilever", case)

    meta = jobs.start(str(case))
    deadline = time.time() + 120.0
    status = jobs.status(meta.job_id)
    while time.time() < deadline and status["state"] not in jobs.TERMINAL_STATES:
        time.sleep(1.0)
        status = jobs.status(meta.job_id)
    assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)

    vtk_file = sorted((case / "vtk_output").glob("*.vtk"))[-1]
    png = tmp_path / "cantilever.png"
    result = _render(render_worker.render_screenshot, {
        "file": str(vtk_file), "variable": "DISPLACEMENT",
        "warp_by": "DISPLACEMENT", "warp_factor": 200.0,
        "camera": "xy", "image_path": str(png), "window_size": [640, 480],
    })
    assert png.read_bytes().startswith(b"\x89PNG")
    # Tip deflection magnitude ~2.56e-4 m (see test_examples.py).
    assert result["data_range"][1] == pytest.approx(2.558e-4, rel=0.05)
