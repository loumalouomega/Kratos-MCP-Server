"""Mesh interchange: meshio import/export and the optional gmsh generator.

meshio is pure Python and safe in the server process. gmsh is a native library
and only runs in ``gmsh_worker.py`` (a plain subprocess, JSON files, no Kratos
env), mirroring how rendering is isolated.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from . import mesh_edit
from .mdpa import Mdpa, SubModelPart, find_part, part_paths

# meshio cell type -> topological dimension (linear, quadratic and the cubic
# line4 / triangle10 / quad16 plus pyramids). meshio 5.3.5 can only store
# triangle10 / quad16 in formats that name them (gmsh .msh; VTK has no such
# cell), and cannot build wedge15 or pyramid13 blocks at all.
_CELL_DIM = {"vertex": 0, "line": 1, "line3": 1, "line4": 1,
             "triangle": 2, "triangle6": 2, "triangle10": 2,
             "quad": 2, "quad8": 2, "quad9": 2, "quad16": 2,
             "tetra": 3, "tetra10": 3, "hexahedron": 3, "hexahedron20": 3,
             "hexahedron27": 3, "wedge": 3, "wedge15": 3,
             "pyramid": 3, "pyramid13": 3, "pyramid14": 3}
# (linear cell kind, node count) -> meshio cell type. Note: ("prism", 15) and
# ("pyr", 13) are deliberately absent: meshio 5.3 has no "wedge15" / "pyramid13"
# CellBlock (its topological_dimension lacks them, so neither meshio.read nor
# meshio.Mesh can carry those cells); to_meshio raises an informative error for
# them unless ``linearize_unsupported`` is set.
_MESHIO_BY_NODES = {
    ("line", 2): "line", ("line", 3): "line3", ("line", 4): "line4",
    ("tri", 3): "triangle", ("tri", 6): "triangle6", ("tri", 10): "triangle10",
    ("quad", 4): "quad", ("quad", 8): "quad8", ("quad", 9): "quad9", ("quad", 16): "quad16",
    ("pyr", 5): "pyramid", ("pyr", 14): "pyramid14",
    ("tet", 4): "tetra", ("tet", 10): "tetra10",
    ("hex", 8): "hexahedron", ("hex", 20): "hexahedron20", ("hex", 27): "hexahedron27",
    ("prism", 6): "wedge",
}
# Linearized replacement for cells meshio cannot carry: (kind, nodes) -> linear type.
_LINEAR_FALLBACK = {("prism", 15): "wedge", ("pyr", 13): "pyramid"}
_DEFAULT_ELEMENT = {"line": "Element2D2N", "line3": "Element2D3N", "line4": "LineElement2D4N",
                    "triangle": "Element2D3N", "triangle6": "Element2D6N",
                    "triangle10": "Element2D10N",
                    "quad": "Element2D4N", "quad8": "Element2D8N", "quad9": "Element2D9N",
                    "quad16": "Element2D16N",
                    "pyramid": "Element3D5N", "pyramid13": "Element3D13N",
                    "pyramid14": "Element3D14N",
                    "tetra": "Element3D4N", "tetra10": "Element3D10N",
                    "hexahedron": "Element3D8N", "hexahedron20": "Element3D20N",
                    "hexahedron27": "Element3D27N",
                    "wedge": "Element3D6N", "wedge15": "Element3D15N"}
_DEFAULT_CONDITION = {"line": "LineCondition2D2N", "line3": "LineCondition2D3N",
                      "line4": "LineCondition2D4N",
                      "triangle": "SurfaceCondition3D3N", "triangle6": "SurfaceCondition3D6N",
                      "quad": "SurfaceCondition3D4N", "quad8": "SurfaceCondition3D8N",
                      "quad9": "SurfaceCondition3D9N"}


class GmshUnavailable(RuntimeError):
    pass


def supported_formats() -> list[str]:
    """File extensions commonly handled by meshio (the full list is meshio's)."""
    return [".msh", ".vtk", ".vtu", ".med", ".inp", ".xdmf", ".obj", ".stl", ".ply", ".off",
            ".mesh", ".exo", ".cgns", ".nas", ".bdf", ".dat", ".su2", ".svg", ".h5m"]


def from_meshio(mesh, element_name: str | None = None, condition_name: str | None = None,
                property_id: int = 1, root_part: str = "domain") -> Mdpa:
    """Convert a ``meshio.Mesh`` into an Mdpa.

    The highest-dimension cells become elements and the next lower dimension
    conditions; vertices only feed point sets. Named cell sets / point sets
    become sub model parts. ``element_name`` / ``condition_name`` override the
    generic defaults (``Element2D3N`` ... ``SurfaceCondition3D4N``)."""
    blocks = [(b.type, np.asarray(b.data)) for b in mesh.cells]
    dims = [_CELL_DIM[t] for t, _ in blocks if t in _CELL_DIM and t != "vertex"]
    unsupported = sorted({t for t, _ in blocks if t not in _CELL_DIM})
    if unsupported:
        raise ValueError(f"Unsupported cell types {unsupported}; converted are linear and "
                         "quadratic line, triangle, quad, tetra, hexahedron, wedge and "
                         "pyramid cells plus the cubic line4, triangle10 and quad16 "
                         "(no other higher-order Lagrange cells)")
    if not dims:
        raise ValueError("The mesh has no line/surface/volume cells")
    top = max(dims)
    pts = np.asarray(mesh.points, dtype=float)
    if pts.shape[1] < 3:
        pts = np.hstack([pts, np.zeros((len(pts), 3 - pts.shape[1]))])
    out = Mdpa()
    for i, xyz in enumerate(pts[:, :3]):
        out.nodes[i + 1] = tuple(float(v) for v in xyz)
    next_e = next_c = 1
    # per block: kind of entity and the ids given to its cells
    block_ids: list[tuple[str, list[int]]] = []
    for (type_name, data) in blocks:
        dim = _CELL_DIM[type_name]
        if dim == top:
            name = element_name or _DEFAULT_ELEMENT[type_name]
            store, kind = out.elements, "element"
        elif dim == top - 1 and type_name in _DEFAULT_CONDITION:
            name = condition_name or _DEFAULT_CONDITION[type_name]
            store, kind = out.conditions, "condition"
        else:
            block_ids.append(("skip", []))
            continue
        bucket = store.setdefault(name, {})
        ids: list[int] = []
        for row in data:
            nodes = [int(n) + 1 for n in row]
            if kind == "element":
                bucket[next_e] = (property_id, nodes)
                ids.append(next_e)
                next_e += 1
            else:
                bucket[next_c] = (property_id, nodes)
                ids.append(next_c)
                next_c += 1
        block_ids.append((kind, ids))
    sets: dict[str, list[Any]] = dict(getattr(mesh, "cell_sets", {}) or {})
    if not sets and "gmsh:physical" in (mesh.cell_data or {}):
        tags = mesh.cell_data["gmsh:physical"]
        for tag in sorted({int(t) for arr in tags for t in arr}):
            sets[f"physical_{tag}"] = [np.nonzero(np.asarray(arr) == tag)[0] for arr in tags]
    for name, per_block in sets.items():
        if name.startswith("gmsh:"):
            continue
        part = SubModelPart(name.replace(" ", "_").replace(".", "_"))
        for (kind, ids), idx in zip(block_ids, per_block):
            if kind == "skip" or idx is None or len(idx) == 0:
                continue
            chosen = [ids[int(i)] for i in idx]
            (part.elements if kind == "element" else part.conditions).extend(chosen)
        if part.elements or part.conditions:
            part.nodes = sorted({n for kind, attr in (("element", part.elements),
                                                      ("condition", part.conditions))
                                 for eid in attr for n in _entry(out, kind, eid)})
            out.sub_model_parts[part.name] = part
    point_sets = getattr(mesh, "point_sets", {}) or {}
    for name, idx in point_sets.items():
        if name.startswith("gmsh:") or len(idx) == 0:
            continue
        key = name.replace(" ", "_").replace(".", "_")
        part = out.sub_model_parts.setdefault(key, SubModelPart(key))
        part.nodes = sorted(set(part.nodes) | {int(i) + 1 for i in idx})
    if root_part not in out.sub_model_parts:
        out.sub_model_parts[root_part] = SubModelPart(
            root_part, nodes=sorted(out.nodes),
            elements=sorted(out.element_ids()))
    return out


def _entry(mesh: Mdpa, kind: str, eid: int) -> list[int]:
    for by_id in (mesh.elements if kind == "element" else mesh.conditions).values():
        if eid in by_id:
            return by_id[eid][1]
    return []


def import_mesh(path: str | Path, file_format: str | None = None, **kwargs: Any) -> Mdpa:
    import meshio

    return from_meshio(meshio.read(str(path), file_format=file_format), **kwargs)


def to_meshio(mesh: Mdpa, linearize_unsupported: bool = False,
              warnings: list[str] | None = None):
    """Mdpa -> meshio.Mesh with elements first, then conditions. Cell data:
    ``kratos:id``, ``kratos:property_id`` and ``kratos:entity`` (0 element, 1
    condition); sub model parts become cell_sets, scalar nodal data point_data
    (vector nodal data as (n, d) arrays when the raw value parses as
    ``[d](...)``). Quadratic and cubic cells map to their meshio type (line3/line4,
    triangle6/triangle10, quad8/quad9/quad16, tetra10, hexahedron20/27, pyramid,
    pyramid14). meshio 5.3 cannot carry 15-node wedges or 13-node pyramids: they raise
    unless ``linearize_unsupported`` is set, which writes their corner-only wedge /
    pyramid instead and appends a note to ``warnings``."""
    import meshio

    import re

    index = {nid: i for i, nid in enumerate(sorted(mesh.nodes))}
    points = np.array([mesh.nodes[n] for n in sorted(mesh.nodes)], dtype=float)
    blocks: dict[tuple[str, int], list[tuple[int, int, list[int]]]] = {}
    for entity, store in ((0, mesh.elements), (1, mesh.conditions)):
        for type_name, by_id in sorted(store.items()):
            for eid, (pid, conn) in sorted(by_id.items()):
                kind = mesh_edit.cell_kind(mesh, type_name, conn)
                cell_type = _MESHIO_BY_NODES.get((kind or "", len(conn)))
                if cell_type is None:
                    fallback = _LINEAR_FALLBACK.get((kind or "", len(conn)))
                    if fallback is not None and linearize_unsupported:
                        conn = mesh_edit.corner_nodes(mesh, type_name, conn)
                        cell_type = fallback
                        note = (f"{type_name}: meshio cannot carry this higher-order cell; "
                                f"exported the corner nodes only as {fallback}")
                        if warnings is not None and note not in warnings:
                            warnings.append(note)
                    elif fallback is not None:
                        what = "15-node wedge" if kind == "prism" else "13-node pyramid"
                        raise ValueError(
                            f"{type_name} {eid} is a {what}, which meshio cannot "
                            "represent; export the .mdpa directly, run mdpa_convert_cells "
                            "mode='quadratic_to_linear' first, or pass "
                            "linearize_unsupported=true to export only the corner nodes")
                    else:
                        raise ValueError(f"{type_name} {eid} ({len(conn)} nodes) has no "
                                         "meshio cell type (linear and quadratic line, "
                                         "triangle, quad, tetra, hexahedron, wedge and pyramid "
                                         "cells, plus line4, triangle10 and quad16)")
                blocks.setdefault((cell_type, entity), []).append((eid, pid, conn))
    cells, ids, pids, ent, locator = [], [], [], [], {}
    for block, ((cell_type, entity), rows) in enumerate(sorted(blocks.items(), key=lambda kv: kv[0][1])):
        cells.append((cell_type, np.array([[index[n] for n in c] for _, _, c in rows], dtype=int)))
        ids.append(np.array([e for e, _, _ in rows]))
        pids.append(np.array([p for _, p, _ in rows]))
        ent.append(np.full(len(rows), entity))
        for local, (eid, _, _) in enumerate(rows):
            locator[(entity, eid)] = (block, local)
    cell_sets: dict[str, list[np.ndarray]] = {}
    for path in part_paths(mesh):
        part = find_part(mesh, path)
        per_block: list[list[int]] = [[] for _ in cells]
        for entity, members in ((0, part.elements), (1, part.conditions)):
            for eid in members:
                if (entity, eid) in locator:
                    block, local = locator[(entity, eid)]
                    per_block[block].append(local)
        if any(per_block):
            cell_sets[path] = [np.array(p, dtype=int) for p in per_block]
    point_data = {var: np.array([vals.get(n, 0.0) for n in sorted(mesh.nodes)])
                  for var, vals in mesh.nodal_data.items()}
    for var, by_id in mesh.vector_nodal_data.items():
        parsed = {}
        for nid, (_, raw) in by_id.items():
            m = re.fullmatch(r"\[\d+\]\((.*)\)", raw.strip())
            if m:
                try:
                    parsed[nid] = [float(x) for x in m.group(1).split(",")]
                except ValueError:
                    parsed = {}
                    break
            else:
                parsed = {}
                break
        if parsed and len({len(v) for v in parsed.values()}) == 1:
            dim = len(next(iter(parsed.values())))
            point_data[var] = np.array(
                [parsed.get(n, [0.0] * dim) for n in sorted(mesh.nodes)])
    return meshio.Mesh(points, cells, point_data=point_data,
                       cell_data={"kratos:id": ids, "kratos:property_id": pids,
                                  "kratos:entity": ent},
                       cell_sets=cell_sets)


def export_mesh(mesh: Mdpa, path: str | Path, file_format: str | None = None,
                linearize_unsupported: bool = False,
                warnings: list[str] | None = None) -> Path:
    import meshio

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mio = to_meshio(mesh, linearize_unsupported, warnings)
    if file_format is None and target.suffix.lower() == ".msh":
        file_format = "gmsh"  # meshio would otherwise pick Ansys for an ambiguous .msh
    try:
        meshio.write(str(target), mio, file_format=file_format)
    except KeyError as exc:
        cubic = sorted({c.type for c in mio.cells} & {"line4", "triangle10", "quad16"})
        if not cubic:
            raise
        target.unlink(missing_ok=True)
        raise ValueError(f"meshio cannot write {cubic} cells to {target.suffix or file_format} "
                         "(no such cell type in that format); use gmsh .msh instead") from exc
    return target


# ----------------------------------------------------------------------- gmsh

def gmsh_available() -> bool:
    """True when the interpreter can import gmsh (checked in a subprocess: the
    import loads native libraries)."""
    proc = subprocess.run([sys.executable, "-c", "import gmsh"], capture_output=True, text=True)
    return proc.returncode == 0


def gmsh_generate(request: dict[str, Any], timeout: float = 300.0, **import_kwargs: Any
                  ) -> tuple[Mdpa, dict[str, Any]]:
    """Run gmsh_worker for ``request`` and import the resulting mesh. Raises
    GmshUnavailable when gmsh is missing and RuntimeError on worker failure."""
    with tempfile.TemporaryDirectory(prefix="kratos-mcp-gmsh-") as scratch:
        scratch_dir = Path(scratch)
        req_file, res_file = scratch_dir / "request.json", scratch_dir / "result.json"
        payload = dict(request, output=str(scratch_dir / "mesh.msh"))
        req_file.write_text(json.dumps(payload))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "kratos_mcp.gmsh_worker", "--request-file", str(req_file),
                 "--result-file", str(res_file)],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"gmsh timed out after {timeout:.0f}s") from None
        if not res_file.exists():
            tail = proc.stderr[-1500:]
            if "No module named 'gmsh'" in tail or "libGLU" in tail or "ImportError" in tail \
                    or "OSError" in tail:
                raise GmshUnavailable(tail)
            raise RuntimeError(f"gmsh worker produced no result (exit {proc.returncode}): {tail}")
        result = json.loads(res_file.read_text())
        if not result.get("ok"):
            error = result.get("error", "unknown error")
            if "No module named 'gmsh'" in error or "libGLU" in error or "cannot open shared" in error:
                raise GmshUnavailable(error)
            raise RuntimeError(error)
        info = result["result"]
        mesh = import_mesh(info["msh"], **import_kwargs)
        info = {k: v for k, v in info.items() if k != "msh"}
        return mesh, info
