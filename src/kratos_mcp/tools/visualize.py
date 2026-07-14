"""Visualization tools: render VTK/VTU results to PNG screenshots and GIF
animations with pyvista (optional 'viz' extra).

Rendering runs in a subprocess (render_worker.py) because VTK's GL setup
can abort the whole process; results travel via a JSON file. On success
the tools return [metadata dict, Image] so MCP clients display the image
inline — this mixed return needs @mcp.tool(structured_output=False)."""

from __future__ import annotations

import glob as globmodule
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio

_CAMERAS = ("xy", "xz", "yz", "iso")
_COMPONENTS = ("x", "y", "z", "magnitude")
_GL_ERROR_RE = re.compile(
    r"OpenGL|\bGL\b|GLX|EGL|xcb|X server|display|render window|segmentation", re.I)
# ImageContent is base64 (~33% overhead); larger GIFs are left on disk only.
_INLINE_GIF_LIMIT = 1_500_000

_INSTALL_HINT = (
    "pyvista is not installed. Install the visualization extra: "
    "pip install 'kratos-mcp-server[viz]', or from a checkout: uv sync --extra viz"
)
_GL_HINT = (
    "Rendering needs a working OpenGL context. On headless machines install "
    "Xvfb (e.g. 'sudo apt install xvfb') — the render worker starts it "
    "automatically when no display is available — or install OSMesa VTK "
    "wheels: pip install --extra-index-url https://wheels.vtk.org vtk-osmesa"
)


def _pyvista_missing() -> dict[str, Any] | None:
    if importlib.util.find_spec("pyvista") is None:
        return {"error": _INSTALL_HINT}
    return None


def _frame_sort_key(path: Path) -> tuple[int, ...]:
    """Numeric timestep order: Structure_0_10 sorts after Structure_0_2."""
    return tuple(int(n) for n in re.findall(r"\d+", path.stem)) or (0,)


def _expand_frames(files: str) -> list[Path]:
    """Expand a directory or glob into result files in timestep order."""
    p = Path(files).expanduser()
    if p.is_dir():
        found = [f for pattern in ("*.vtk", "*.vtu") for f in p.glob(pattern)]
    else:
        found = [Path(m) for m in globmodule.glob(str(p))]
    return sorted((f.resolve() for f in found if f.is_file()), key=_frame_sort_key)


def _render_failure(message: str, stderr: str) -> dict[str, Any]:
    out: dict[str, Any] = {"error": message}
    if stderr.strip():
        out["stderr_tail"] = stderr[-2000:]
    if _GL_ERROR_RE.search(message) or _GL_ERROR_RE.search(stderr):
        out["hint"] = _GL_HINT
    return out


def _run_render(op: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run the render worker in a subprocess; JSON request/result files,
    never stdout (VTK prints warnings there)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="kratos-mcp-render-", delete=False
    ) as tf:
        request_file = Path(tf.name)
        json.dump({"op": op, "args": args}, tf)
    result_file = request_file.with_suffix(".out.json")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "kratos_mcp.render_worker",
             "--request-file", str(request_file), "--result-file", str(result_file)],
            capture_output=True, text=True, timeout=timeout,
        )
        if not result_file.exists():
            return _render_failure(
                f"Render worker produced no result (exit code {proc.returncode}).",
                proc.stderr)
        payload = json.loads(result_file.read_text())
        if not payload.get("ok"):
            return _render_failure(
                f"Rendering failed: {payload.get('error', 'unknown error')}",
                proc.stderr)
        return payload["result"]
    except subprocess.TimeoutExpired:
        return {"error": f"Rendering timed out after {timeout:.0f}s"}
    finally:
        for f in (request_file, result_file):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_view(camera: str, component: str | None) -> dict[str, Any] | None:
    if camera not in _CAMERAS:
        return {"error": f"camera must be one of {list(_CAMERAS)}"}
    if component is not None and component not in _COMPONENTS:
        return {"error": f"component must be one of {list(_COMPONENTS)}"}
    return None


def register(mcp) -> None:
    from mcp.server.fastmcp import Image

    @mcp.tool(structured_output=False)
    async def results_render(
        file: str,
        variable: str | None = None,
        component: str | None = None,
        warp_by: str | None = None,
        warp_factor: float = 1.0,
        camera: str = "iso",
        image_path: str | None = None,
        window_size: list[int] | None = None,
        show_edges: bool = True,
        crop_bounds: list[float] | None = None,
    ) -> Any:
        """Render a VTK/VTU result file to a PNG screenshot, returned inline
        (requires the optional pyvista 'viz' extra). Colors the mesh by
        'variable' (point or cell data; vector fields default to magnitude,
        or pick component 'x'/'y'/'z'), optionally warps the geometry by a
        vector field such as DISPLACEMENT scaled by warp_factor. Camera
        presets: xy, xz, yz, iso. crop_bounds clips to a region of interest
        before framing the camera -- [xmin,xmax,ymin,ymax] or
        [xmin,xmax,ymin,ymax,zmin,zmax] -- essential for e.g. a small body in
        a huge far-field CFD domain, otherwise invisible at full-domain zoom.
        The PNG is saved next to the input file unless image_path is given."""
        missing = _pyvista_missing()
        if missing:
            return missing
        p = Path(file).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        invalid = _validate_view(camera, component)
        if invalid:
            return invalid
        out = (Path(image_path).expanduser().resolve() if image_path
               else p.parent / f"{p.stem}_{variable or 'mesh'}.png")
        args = {
            "file": str(p), "variable": variable, "component": component,
            "warp_by": warp_by, "warp_factor": warp_factor, "camera": camera,
            "image_path": str(out), "window_size": window_size or [1024, 768],
            "show_edges": show_edges, "crop_bounds": crop_bounds,
        }
        result = await anyio.to_thread.run_sync(
            lambda: _run_render("screenshot", args, timeout=120.0))
        if "error" in result:
            return result
        meta = {"file": str(p), "variable": variable, "component": component,
                "camera": camera, "warp_by": warp_by, "warp_factor": warp_factor,
                "window_size": args["window_size"], "crop_bounds": crop_bounds, **result}
        return [meta, Image(path=result["image_path"])]

    @mcp.tool(structured_output=False)
    async def results_animate(
        files: str,
        variable: str | None = None,
        component: str | None = None,
        warp_by: str | None = None,
        warp_factor: float = 1.0,
        camera: str = "iso",
        gif_path: str | None = None,
        fps: int = 5,
        window_size: list[int] | None = None,
        show_edges: bool = True,
        max_frames: int = 50,
        crop_bounds: list[float] | None = None,
    ) -> Any:
        """Render a time series of VTK/VTU results into an animated GIF
        (requires the optional pyvista 'viz' extra). 'files' is a directory
        (e.g. the case's vtk_output/) or a glob; frames are ordered by the
        numbers in their names. Coloring/warping/crop_bounds options as in
        results_render, with one color range, crop and camera across all
        frames. Small GIFs are returned inline; the file path is always
        returned."""
        missing = _pyvista_missing()
        if missing:
            return missing
        invalid = _validate_view(camera, component)
        if invalid:
            return invalid
        frames = _expand_frames(files)
        if not frames:
            return {"error": f"No .vtk/.vtu files match {files}"}
        truncated = len(frames) > max_frames
        frames = frames[:max_frames]
        if gif_path:
            out = Path(gif_path).expanduser().resolve()
        else:
            base = re.sub(r"[_\d]+$", "", frames[0].stem) or "animation"
            out = frames[0].parent / f"{base}_{variable or 'mesh'}.gif"
        args = {
            "files": [str(f) for f in frames], "variable": variable,
            "component": component, "warp_by": warp_by, "warp_factor": warp_factor,
            "camera": camera, "gif_path": str(out), "fps": fps,
            "window_size": window_size or [800, 600], "show_edges": show_edges,
            "crop_bounds": crop_bounds,
        }
        timeout = min(60.0 + 10.0 * len(frames), 600.0)
        result = await anyio.to_thread.run_sync(
            lambda: _run_render("gif", args, timeout=timeout))
        if "error" in result:
            return result
        meta = {"files": files, "variable": variable, "component": component,
                "camera": camera, "warp_by": warp_by, "warp_factor": warp_factor,
                "window_size": args["window_size"], "crop_bounds": crop_bounds, **result}
        if truncated:
            meta["note"] = (f"{len(_expand_frames(files))} files matched; only the "
                            f"first {max_frames} frames were rendered (max_frames)")
        gif = Path(result["gif_path"])
        if gif.stat().st_size > _INLINE_GIF_LIMIT:
            size_mb = gif.stat().st_size / 1e6
            meta.setdefault("note", "")
            meta["note"] = (meta["note"] + " " if meta["note"] else "") + (
                f"GIF is {size_mb:.1f} MB — too large to return inline; "
                f"open gif_path directly")
            return meta
        return [meta, Image(path=str(gif))]
