# Visualization

Preview results without opening ParaView: render VTK/VTU files to PNG
screenshots and GIF animations with [pyvista](https://pyvista.org). The
image is saved to disk **and** returned inline as MCP image content, so
assistants that support images (e.g. Claude) display the preview directly
in the conversation.

These tools need the optional `viz` extra:

```bash
pip install 'kratos-mcp-server[viz]'
# or, from a checkout:
uv sync --extra viz
```

Without it they return an error explaining how to install it. Everything
else in the server works without pyvista.

## results_render

Render one VTK/VTU result file to a PNG screenshot.

| Parameter | Type | Description |
| --- | --- | --- |
| `file` | string | result file (`.vtk`/`.vtu`) |
| `variable` | string? | point or cell variable to color by; omit for plain geometry |
| `component` | string? | `x`, `y`, `z` or `magnitude` for vector fields (default: magnitude) |
| `warp_by` | string? | 3-component point vector to warp the geometry by, e.g. `DISPLACEMENT` |
| `warp_factor` | number | warp scale factor (default 1.0) |
| `camera` | string | `xy`, `xz`, `yz` or `iso` (default) |
| `image_path` | string? | output PNG (default: next to the input file) |
| `window_size` | int[2]? | pixels, default `[1024, 768]` |
| `show_edges` | boolean | draw element edges (default true) |

**Returns**: the PNG as inline image content, plus a JSON metadata block:

```json
// results_render(".../Structure_0_1.vtk", variable="DISPLACEMENT",
//                warp_by="DISPLACEMENT", warp_factor=200, camera="xy") → (excerpt)
{
  "image_path": ".../vtk_output/Structure_0_1_DISPLACEMENT.png",
  "variable": "DISPLACEMENT",
  "data_range": [0.0, 2.558e-4],
  "camera": "xy"
}
```

Static analyses barely move at true scale — pass a `warp_factor` large
enough to make the deformation visible (the `data_range` from a first
render or `results_summary` tells you the magnitude).

## results_animate

Render a time series of results into an animated GIF, with one color range
and camera across all frames so the animation is stable.

| Parameter | Type | Description |
| --- | --- | --- |
| `files` | string | a directory (e.g. the case's `vtk_output/`) or a glob like `.../vtk_output/*.vtk` |
| `variable` | string? | as in `results_render` |
| `component` | string? | as in `results_render` |
| `warp_by` | string? | as in `results_render` |
| `warp_factor` | number | as in `results_render` |
| `camera` | string | as in `results_render` |
| `gif_path` | string? | output GIF (default: next to the frames) |
| `fps` | int | frames per second (default 5) |
| `window_size` | int[2]? | pixels, default `[800, 600]` |
| `show_edges` | boolean | draw element edges (default true) |
| `max_frames` | int | cap on rendered frames (default 50) |

Frames are ordered by the numbers in their file names (`Structure_0_2.vtk`
before `Structure_0_10.vtk`), matching Kratos' `<ModelPart>_<rank>_<step>`
naming.

**Returns**: metadata (`gif_path`, `num_frames`, `fps`, `data_range`) plus
the GIF inline when it is small enough (≲1.5 MB); larger GIFs are only
written to `gif_path`, with a note saying so.

## Headless rendering

Rendering happens in a **subprocess** (`render_worker.py`), never in the
server process: VTK's OpenGL setup can abort the whole process on
misconfigured systems, and the server must survive that (same reasoning as
the Kratos import ban described in [Architecture](/guide/architecture)).

A working OpenGL context is required. On desktops (and WSL2 with WSLg)
this just works. On truly headless machines, either:

- install **Xvfb** (`sudo apt install xvfb`) — the render worker starts a
  private Xvfb automatically when no display is available, or
- install the OSMesa software-rendering VTK wheels:
  `pip install --extra-index-url https://wheels.vtk.org vtk-osmesa`.

If rendering fails, the error includes the worker's stderr tail and this
hint.
