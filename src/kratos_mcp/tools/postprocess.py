"""Post-processing tools: discover and read simulation results.

VTK/VTU files (the default output of our templates) are read with meshio;
statistics use numpy. Convergence information is parsed from job logs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio

from .. import jobs, logparse

_RESULT_PATTERNS = {
    "vtk": ("*.vtk", "*.vtu"),
    "gid": ("*.post.bin", "*.post.res", "*.post.msh"),
    "hdf5": ("*.h5", "*.hdf5"),
    "json": ("*results*.json", "*output*.json"),
    "dat": ("*.dat", "*.csv"),
}


def _read_mesh(file: str):
    import meshio

    return meshio.read(file)


def _vector_stats(arr) -> dict[str, Any]:
    import numpy as np

    a = np.asarray(arr, dtype=float)
    stats: dict[str, Any] = {"shape": list(a.shape)}
    if a.ndim == 1:
        stats.update(min=float(a.min()), max=float(a.max()), mean=float(a.mean()))
        stats["abs_max"] = float(np.abs(a).max())
    else:
        mag = np.linalg.norm(a, axis=1)
        stats.update(
            min_magnitude=float(mag.min()), max_magnitude=float(mag.max()),
            mean_magnitude=float(mag.mean()),
            component_min=[float(v) for v in a.min(axis=0)],
            component_max=[float(v) for v in a.max(axis=0)],
        )
    return stats


def register(mcp) -> None:

    @mcp.tool()
    def results_list(case_dir: str) -> dict[str, Any]:
        """Discover result artifacts in a case directory (recursively):
        VTK/VTU files, GiD post files, HDF5, JSON results and point-output
        .dat/.csv files, sorted by name so timesteps appear in order."""
        case = Path(case_dir).expanduser().resolve()
        if not case.is_dir():
            return {"error": f"{case} is not a directory"}
        out: dict[str, list[str]] = {}
        for kind, patterns in _RESULT_PATTERNS.items():
            files: list[str] = []
            for pattern in patterns:
                files += [str(p.relative_to(case)) for p in case.rglob(pattern)]
            if files:
                out[kind] = sorted(set(files))
        return {"case_dir": str(case), "results": out}

    @mcp.tool()
    async def results_summary(file: str, variable: str | None = None) -> dict[str, Any]:
        """Summarise a VTK/VTU result file: number of points/cells, the
        variables present, and per-variable statistics (min/max/mean, or
        magnitude stats for vector fields). Optionally restrict to one
        variable."""
        p = Path(file).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        try:
            mesh = await anyio.to_thread.run_sync(_read_mesh, str(p))
        except Exception as exc:
            return {"error": f"Could not read {p.name}: {exc}"}
        out: dict[str, Any] = {
            "file": str(p),
            "num_points": len(mesh.points),
            "num_cells": int(sum(len(block.data) for block in mesh.cells)),
            "point_variables": sorted(mesh.point_data),
            "cell_variables": sorted(mesh.cell_data),
        }
        stats: dict[str, Any] = {}
        for name, arr in mesh.point_data.items():
            if variable and name != variable:
                continue
            stats[name] = _vector_stats(arr)
        out["statistics"] = stats
        if variable and variable not in stats:
            out["warning"] = (f"Variable '{variable}' not found; available: "
                              f"{sorted(mesh.point_data)}")
        return out

    @mcp.tool()
    async def results_probe(
        file: str,
        variable: str,
        point: list[float] | None = None,
        node_index: int | None = None,
    ) -> dict[str, Any]:
        """Read the value of a variable at one location of a VTK/VTU result:
        either the mesh point nearest to 'point' [x, y, z], or the point at
        0-based index 'node_index'. Returns the value and the actual
        coordinates used."""
        import numpy as np

        p = Path(file).expanduser().resolve()
        if not p.is_file():
            return {"error": f"{p} does not exist"}
        try:
            mesh = await anyio.to_thread.run_sync(_read_mesh, str(p))
        except Exception as exc:
            return {"error": f"Could not read {p.name}: {exc}"}
        if variable not in mesh.point_data:
            return {"error": f"Variable '{variable}' not in file; available: "
                             f"{sorted(mesh.point_data)}"}
        pts = np.asarray(mesh.points, dtype=float)
        if node_index is not None:
            if not 0 <= node_index < len(pts):
                return {"error": f"node_index {node_index} out of range 0..{len(pts)-1}"}
            idx = node_index
            distance = 0.0
        elif point is not None:
            target = np.zeros(3)
            target[: len(point)] = point
            dists = np.linalg.norm(pts - target, axis=1)
            idx = int(dists.argmin())
            distance = float(dists[idx])
        else:
            return {"error": "Provide either 'point' [x, y, z] or 'node_index'"}
        value = np.asarray(mesh.point_data[variable])[idx]
        return {
            "variable": variable,
            "node_index": int(idx),
            "coordinates": [float(v) for v in pts[idx]],
            "distance_from_query": distance,
            "value": value.tolist() if hasattr(value, "tolist") else float(value),
        }

    @mcp.tool()
    def results_convergence(
        job_id: str | None = None, log_file: str | None = None
    ) -> dict[str, Any]:
        """Extract convergence information from a simulation log (by job_id
        or explicit log file path): per-step nonlinear iteration counts,
        residual ratios, and which steps converged."""
        if job_id:
            try:
                text = jobs.log_path(job_id).read_text(errors="replace")
            except (KeyError, OSError) as exc:
                return {"error": str(exc)}
        elif log_file:
            p = Path(log_file).expanduser().resolve()
            if not p.is_file():
                return {"error": f"{p} does not exist"}
            text = p.read_text(errors="replace")
        else:
            return {"error": "Provide either job_id or log_file"}
        result = logparse.convergence(text)
        result.update(logparse.progress(text))
        return result
