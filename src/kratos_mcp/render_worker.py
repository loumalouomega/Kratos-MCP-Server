"""Render worker process: the ONLY code that imports pyvista/VTK.

VTK's OpenGL initialisation can abort the whole process on headless or
misconfigured systems, so rendering never happens inside the MCP server
(same class of rule as the KratosMultiphysics import ban). Invoked by
tools/visualize.py as `python -m kratos_mcp.render_worker --request-file
req.json --result-file out.json`; the result is written to --result-file
as {"ok": true, "result": ...} or {"ok": false, "error": "..."} — never
stdout, because VTK prints warnings there.

If no display is available and Xvfb is installed, main() starts a private
Xvfb server for the duration of the render (pyvista 0.48 removed
start_xvfb, so we manage it ourselves)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from typing import Any

_COMPONENT_INDEX = {"x": 0, "y": 1, "z": 2}


def _maybe_warp(mesh, args: dict[str, Any]):
    """Warp the mesh by a 3-component point vector (e.g. DISPLACEMENT)."""
    import numpy as np

    warp_by = args.get("warp_by")
    if not warp_by:
        return mesh
    if warp_by not in mesh.point_data:
        raise ValueError(f"warp_by variable '{warp_by}' not in point data; "
                         f"available: {sorted(mesh.point_data)}")
    arr = np.asarray(mesh.point_data[warp_by])
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"warp_by variable '{warp_by}' is not a 3-component vector "
                         f"(shape {list(arr.shape)})")
    return mesh.warp_by_vector(warp_by, factor=float(args.get("warp_factor", 1.0)))


def _maybe_crop(mesh, args: dict[str, Any]):
    """Clip to a region of interest — e.g. a small airfoil in a huge far-field
    domain is otherwise an invisible speck once the camera fits the whole mesh."""
    bounds = args.get("crop_bounds")
    if not bounds:
        return mesh
    if len(bounds) == 4:
        xmin, xmax, ymin, ymax = bounds
        zmin, zmax = mesh.bounds[4], mesh.bounds[5]
        bounds = [xmin, xmax, ymin, ymax, zmin, zmax]
    if len(bounds) != 6:
        raise ValueError("crop_bounds must have 4 [xmin,xmax,ymin,ymax] or "
                         "6 [xmin,xmax,ymin,ymax,zmin,zmax] entries")
    return mesh.clip_box(bounds, invert=False)


def _resolve_scalars(mesh, variable: str | None, component: str | None):
    """Return (scalar array name to color by, [min, max]). Vector variables
    get a derived scalar array: one component, or the magnitude by default."""
    import numpy as np

    if variable is None:
        return None, None
    if variable in mesh.point_data:
        data = mesh.point_data
    elif variable in mesh.cell_data:
        data = mesh.cell_data
    else:
        available = sorted(mesh.point_data) + sorted(mesh.cell_data)
        raise ValueError(f"Variable '{variable}' not in file; available: {available}")
    arr = np.asarray(data[variable])
    name = variable
    if arr.ndim > 1:
        comp = component or "magnitude"
        if comp == "magnitude":
            arr = np.linalg.norm(arr, axis=1)
        else:
            idx = _COMPONENT_INDEX[comp]
            if idx >= arr.shape[1]:
                raise ValueError(f"Variable '{variable}' has {arr.shape[1]} "
                                 f"components; no '{comp}'")
            arr = arr[:, idx]
        name = f"{variable}_{comp}"
        data[name] = arr
    return name, [float(arr.min()), float(arr.max())]


def _prepare(file: str, args: dict[str, Any]):
    import pyvista as pv

    mesh = pv.read(file)
    mesh = _maybe_warp(mesh, args)
    mesh = _maybe_crop(mesh, args)
    name, data_range = _resolve_scalars(mesh, args.get("variable"), args.get("component"))
    return mesh, name, data_range


def _add_mesh(plotter, mesh, scalars_name: str | None, args: dict[str, Any],
              clim: list[float] | None = None) -> None:
    kwargs: dict[str, Any] = {"show_edges": bool(args.get("show_edges", True))}
    if scalars_name is not None:
        kwargs.update(scalars=scalars_name, cmap="viridis",
                      scalar_bar_args={"title": scalars_name})
        if clim is not None:
            kwargs["clim"] = clim
    else:
        kwargs["color"] = "lightsteelblue"
    plotter.add_mesh(mesh, **kwargs)


def _apply_camera(plotter, camera: str) -> None:
    {
        "xy": plotter.view_xy,
        "xz": plotter.view_xz,
        "yz": plotter.view_yz,
        "iso": plotter.view_isometric,
    }[camera]()


def render_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    import pyvista as pv

    pv.OFF_SCREEN = True
    mesh, name, data_range = _prepare(args["file"], args)
    image_path = args["image_path"]
    os.makedirs(os.path.dirname(image_path) or ".", exist_ok=True)
    plotter = pv.Plotter(off_screen=True, window_size=args.get("window_size") or [1024, 768])
    _add_mesh(plotter, mesh, name, args)
    _apply_camera(plotter, args.get("camera", "iso"))
    plotter.screenshot(image_path)
    plotter.close()
    return {"image_path": image_path, "data_range": data_range}


def render_gif(args: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import pyvista as pv

    pv.OFF_SCREEN = True
    frames = [_prepare(f, args) for f in args["files"]]
    if not frames:
        raise ValueError("No frames to render")
    ranges = [r for _, _, r in frames if r is not None]
    # One global color range and camera so the animation is stable.
    clim = [min(r[0] for r in ranges), max(r[1] for r in ranges)] if ranges else None
    all_bounds = np.array([mesh.bounds for mesh, _, _ in frames])
    bounds = [float(all_bounds[:, i].min() if i % 2 == 0 else all_bounds[:, i].max())
              for i in range(6)]

    gif_path = args["gif_path"]
    os.makedirs(os.path.dirname(gif_path) or ".", exist_ok=True)
    fps = int(args.get("fps", 5))
    plotter = pv.Plotter(off_screen=True, window_size=args.get("window_size") or [800, 600])
    plotter.open_gif(gif_path, fps=fps)
    for i, (mesh, name, _) in enumerate(frames):
        plotter.clear_actors()
        _add_mesh(plotter, mesh, name, args, clim=clim)
        if i == 0:
            _apply_camera(plotter, args.get("camera", "iso"))
            plotter.reset_camera(bounds=bounds)
        plotter.write_frame()
    plotter.close()
    return {"gif_path": gif_path, "num_frames": len(frames), "fps": fps,
            "data_range": clim}


def _start_xvfb_if_needed() -> subprocess.Popen | None:
    """Start a private Xvfb when there is no display at all. -displayfd
    makes Xvfb pick a free display number and report it back, race-free."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return None
    if shutil.which("Xvfb") is None:
        return None  # attempt the render anyway; a GL failure gets reported
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x1024x24"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    with os.fdopen(read_fd) as f:
        display = f.readline().strip()
    os.environ["DISPLAY"] = f":{display}"
    return proc


OPS = {
    "screenshot": render_screenshot,
    "gif": render_gif,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--result-file", required=True)
    ns = parser.parse_args()

    with open(ns.request_file) as f:
        request = json.load(f)

    xvfb = None
    payload: dict[str, Any]
    try:
        op = request["op"]
        if op not in OPS:
            raise KeyError(f"Unknown op '{op}'. Available: {sorted(OPS)}")
        xvfb = _start_xvfb_if_needed()
        payload = {"ok": True, "result": OPS[op](request.get("args", {}))}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()}
    finally:
        if xvfb is not None:
            xvfb.terminate()

    with open(ns.result_file, "w") as f:
        json.dump(payload, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
