"""gmsh worker process: the ONLY code that imports gmsh.

gmsh is a large native library (OpenCASCADE, optional OpenGL/X11 pieces) that
can abort the process on a bad install or a broken CAD file, so it never runs
inside the MCP server (same class of rule as the Kratos and pyvista imports).
Invoked by ``mesh_io.gmsh_generate`` as ``python -m kratos_mcp.gmsh_worker
--request-file req.json --result-file out.json``; the answer goes to
--result-file as {"ok": true, "result": ...} or {"ok": false, "error": ...},
never stdout (gmsh logs there).

Request kinds
  polygon  {"vertices": [[x, y], ...], "holes": [[[x, y], ...], ...], "size": h,
            "quads": false}
           physical groups: ``domain`` (surface), ``side_<i>`` per outer edge,
           ``hole_<k>`` per hole loop.
  shape    {"shape": "box"|"cylinder"|"sphere", ...} 3D tetrahedra; groups
           ``domain`` (volume) and ``boundary`` (all faces).
  file     {"path": "...step|brep|geo|msh|stl", "dim": 2|3, "size": h}; existing
           physical groups are kept, otherwise ``domain``/``boundary`` are made.
Every request writes the mesh to "output" as ASCII MSH 4.1.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def _set_sizes(gmsh, args: dict[str, Any]) -> None:
    size = args.get("size")
    if size:
        gmsh.option.setNumber("Mesh.MeshSizeMax", float(size))
        gmsh.option.setNumber("Mesh.MeshSizeMin", float(args.get("size_min") or size / 10.0))
    elif args.get("size_min"):
        gmsh.option.setNumber("Mesh.MeshSizeMin", float(args["size_min"]))
    if args.get("quads"):
        gmsh.option.setNumber("Mesh.RecombineAll", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 8)


def _polygon(gmsh, args: dict[str, Any]) -> int:
    occ = gmsh.model.occ
    size = float(args.get("size") or 0.0)

    def loop(points: list[list[float]]) -> tuple[int, list[int]]:
        pts = [occ.addPoint(float(p[0]), float(p[1]), 0.0, size) for p in points]
        lines = [occ.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        return occ.addCurveLoop(lines), lines

    outer, outer_lines = loop(args["vertices"])
    holes = [loop(h) for h in args.get("holes", [])]
    surface = occ.addPlaneSurface([outer] + [h[0] for h in holes])
    occ.synchronize()
    gmsh.model.addPhysicalGroup(2, [surface], name="domain")
    for i, line in enumerate(outer_lines):
        gmsh.model.addPhysicalGroup(1, [line], name=f"side_{i}")
    for k, (_, lines) in enumerate(holes):
        gmsh.model.addPhysicalGroup(1, lines, name=f"hole_{k}")
    return 2


def _shape(gmsh, args: dict[str, Any]) -> int:
    occ = gmsh.model.occ
    kind = args["shape"]
    origin = [float(v) for v in args.get("origin", [0, 0, 0])]
    if kind == "box":
        lx, ly, lz = (float(v) for v in args["size_xyz"])
        volume = occ.addBox(*origin, lx, ly, lz)
    elif kind == "cylinder":
        volume = occ.addCylinder(*origin, 0, 0, float(args["height"]), float(args["radius"]))
    elif kind == "sphere":
        volume = occ.addSphere(*origin, float(args["radius"]))
    else:
        raise ValueError("shape must be box, cylinder or sphere")
    occ.synchronize()
    gmsh.model.addPhysicalGroup(3, [volume], name="domain")
    faces = [tag for _, tag in gmsh.model.getBoundary([(3, volume)], oriented=False)]
    gmsh.model.addPhysicalGroup(2, faces, name="boundary")
    return 3


def _file(gmsh, args: dict[str, Any]) -> int:
    path = str(args["path"])
    dim = int(args.get("dim", 3))
    if Path(path).suffix.lower() in (".step", ".stp", ".brep", ".iges", ".igs"):
        gmsh.model.occ.importShapes(path)
        gmsh.model.occ.synchronize()
    else:
        gmsh.open(path)
    if not gmsh.model.getPhysicalGroups():
        entities = gmsh.model.getEntities(dim)
        if not entities:
            raise ValueError(f"the file has no entities of dimension {dim}")
        gmsh.model.addPhysicalGroup(dim, [t for _, t in entities], name="domain")
        faces = [t for _, t in gmsh.model.getBoundary(entities, oriented=False)]
        if faces:
            gmsh.model.addPhysicalGroup(dim - 1, faces, name="boundary")
    return dim


def run(request: dict[str, Any]) -> dict[str, Any]:
    import gmsh

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.option.setNumber("Mesh.Binary", 0)
        gmsh.model.add("kratos_mcp")
        kind = request["kind"]
        builder = {"polygon": _polygon, "shape": _shape, "file": _file}.get(kind)
        if builder is None:
            raise ValueError("kind must be polygon, shape or file")
        dim = builder(gmsh, request)
        _set_sizes(gmsh, request)
        gmsh.model.mesh.generate(dim)
        output = request["output"]
        gmsh.write(output)
        groups = [{"dim": d, "tag": t, "name": gmsh.model.getPhysicalName(d, t)}
                  for d, t in gmsh.model.getPhysicalGroups()]
        node_tags, _, _ = gmsh.model.mesh.getNodes()
        return {"msh": output, "dimension": dim, "physical_groups": groups,
                "num_nodes": int(len(node_tags)), "gmsh_version": gmsh.__version__}
    finally:
        gmsh.finalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--result-file", required=True)
    ns = parser.parse_args()
    try:
        request = json.loads(Path(ns.request_file).read_text())
        payload: dict[str, Any] = {"ok": True, "result": run(request)}
    except Exception as exc:  # noqa: BLE001 - reported to the caller as data
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()[-2000:]}
    Path(ns.result_file).write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
