"""Geometry and mesh-interchange tools: parametric shapes, meshio import/export,
and the optional gmsh generator. No Kratos involved."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import anyio

from .. import geometry as geo, mesh_io
from .mesh_edit import _finish, _tool_errors, _load


def _new_mdpa_path(output: str, overwrite: bool) -> Path:
    dst = Path(output).expanduser().resolve()
    if dst.suffix != ".mdpa":
        dst = dst.with_suffix(".mdpa")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} already exists; pass overwrite=true to replace it")
    return dst


GMSH_HINT = ("gmsh is not available. Install the optional extra (uv sync --extra gmsh, or "
             "pip install gmsh); on Linux it also needs libGLU/libXft system libraries.")


def register(mcp) -> None:

    @mcp.tool()
    @_tool_errors
    def mdpa_create_geometry_mesh(
        path: str,
        kind: str,
        params: dict[str, Any],
        element_name: str | None = None,
        condition_name: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Generate a mesh of a parametric shape and write it as .mdpa (no gmsh
        needed). Every mesh gets a 'domain' part plus named boundary parts with
        conditions of condition_name (default LineLoadCondition2D2N in 2D,
        SurfaceLoadCondition3D4N in 3D). kind and its params:
          'annulus'  {r_inner, r_outer, n_radial, n_theta, angle=360, triangles=false}
                     quads (or triangles); parts inner, outer (+ start, end for angle<360)
          'disc'     {radius, n_radial, n_theta}  triangles; part boundary
          'cylinder' {radius, height, n_radial, n_theta, n_z, inner_radius=None}
                     prisms (solid) or hexahedra (hollow); parts lateral, zmin, zmax
          'sphere'   {radius, divisions}  hexahedra, radially mapped cube; part surface
          'polygon'  {vertices: [[x, y], ...], refinements=0, holes=None,
                     target_size=None}  triangles by ear clipping + midpoint
                     subdivision; parts side_0, side_1, ... plus hole_<k> per hole
                     loop; holes is a list of vertex loops strictly inside the
                     polygon; target_size adds subdivision rounds until no edge
                     is longer than it (at most 6 rounds total, best effort)
        element_name overrides the element type (default: SmallDisplacement
        elements of matching node count). For graded sizes or CAD input
        use mesh_generate_gmsh."""
        if kind not in geo.GENERATORS:
            raise ValueError(f"Unknown kind '{kind}'. Use {sorted(geo.GENERATORS)}")
        fn = geo.GENERATORS[kind]
        accepted = set(inspect.signature(fn).parameters) - {"element_name", "condition_name",
                                                            "property_id", "root_part"}
        unknown = set(params) - accepted
        if unknown:
            raise ValueError(f"Unknown params {sorted(unknown)} for '{kind}'. "
                             f"Accepted: {sorted(accepted)}")
        dst = _new_mdpa_path(path, overwrite)
        kwargs = dict(params)
        if element_name:
            kwargs["element_name"] = element_name
        if kind in ("cylinder", "sphere"):
            default_condition = "SurfaceLoadCondition3D4N"
        else:
            default_condition = "LineLoadCondition2D2N"
        kwargs["condition_name"] = condition_name or default_condition
        mesh = fn(**kwargs)
        return _finish(mesh, dst, None)

    @mcp.tool()
    @_tool_errors
    def mesh_import(
        source: str,
        output: str,
        element_name: str | None = None,
        condition_name: str | None = None,
        property_id: int = 1,
        file_format: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Convert a mesh file of another format (gmsh .msh, VTK .vtk/.vtu, MED,
        Abaqus .inp, Nastran, XDMF, ... anything meshio reads) into a Kratos
        .mdpa. The highest-dimension cells become elements, the next lower
        dimension conditions; named physical groups / cell sets become sub model
        parts (a 'domain' part with everything is added). Linear and quadratic
        line, triangle, quad, tetra, hexahedron and wedge cells are converted
        (15-node wedges cannot be represented by meshio itself and are refused
        on export; cubic and higher Lagrange cells are rejected).
        element_name / condition_name set the Kratos types (defaults are generic
        Element2D3N ... SurfaceCondition3D4N, fine for thermal or fluid solvers
        that replace elements; give e.g. SmallDisplacementElement2D3N for
        structural cases). file_format forces meshio's reader when the extension
        is ambiguous."""
        src = Path(source).expanduser().resolve()
        if not src.is_file():
            raise ValueError(f"{src} does not exist")
        dst = _new_mdpa_path(output, overwrite)
        mesh = mesh_io.import_mesh(src, file_format, element_name=element_name,
                                   condition_name=condition_name, property_id=property_id)
        return _finish(mesh, dst, None)

    @mcp.tool()
    @_tool_errors
    def mesh_export(
        path: str,
        output: str,
        file_format: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Export a .mdpa to another mesh format chosen by the output extension
        (.vtk, .vtu, .msh, .med, .inp, .xdmf, ...) or file_format. Elements come
        first, then conditions; cell data carries kratos:id, kratos:property_id
        and kratos:entity (0 element / 1 condition), sub model parts become cell
        sets (format permitting) and scalar nodal data point data."""
        _, mesh = _load(path)
        dst = Path(output).expanduser().resolve()
        if dst.exists() and not overwrite:
            raise ValueError(f"{dst} already exists; pass overwrite=true to replace it")
        written = mesh_io.export_mesh(mesh, dst, file_format)
        return {"written_to": str(written), "mesh": mesh.inspect()}

    @mcp.tool()
    async def mesh_generate_gmsh(
        output: str,
        kind: str,
        vertices: list[list[float]] | None = None,
        holes: list[list[list[float]]] | None = None,
        shape: str | None = None,
        shape_params: dict[str, Any] | None = None,
        cad_file: str | None = None,
        dim: int = 3,
        size: float | None = None,
        size_min: float | None = None,
        quads: bool = False,
        element_name: str | None = None,
        condition_name: str | None = None,
        property_id: int = 1,
        overwrite: bool = False,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Mesh arbitrary geometry with gmsh (optional extra: uv sync --extra
        gmsh) and write it as .mdpa. kind:
          'polygon' -- vertices [[x, y], ...] (counter-clockwise) with optional
                       holes (list of vertex loops); physical groups become sub
                       model parts: domain, side_<i> (outer edge i), hole_<k>;
                       quads=true recombines into quadrilaterals.
          'shape'   -- 3D tetrahedra of shape 'box' (shape_params {size_xyz:
                       [lx, ly, lz], origin}), 'cylinder' ({radius, height})
                       or 'sphere' ({radius}); parts domain, boundary.
          'file'    -- mesh a CAD/geometry file (cad_file: .step, .brep, .geo,
                       .iges...) in dimension dim; its physical groups are kept,
                       else domain/boundary are created.
        size is the target element size (size_min the lower bound, default
        size/10). element_name / condition_name set the Kratos types (defaults
        Element2D3N / LineCondition2D2N / Element3D4N / SurfaceCondition3D3N).
        gmsh runs in a subprocess, never inside the server. Returns the mesh
        summary and the gmsh physical groups."""
        request: dict[str, Any] = {"kind": kind, "size": size, "size_min": size_min,
                                   "quads": quads}
        if kind == "polygon":
            if not vertices or len(vertices) < 3:
                return {"error": "kind 'polygon' needs vertices with at least 3 points"}
            request.update(vertices=vertices, holes=holes or [])
        elif kind == "shape":
            if not shape:
                return {"error": "kind 'shape' needs shape: box, cylinder or sphere"}
            request.update(shape=shape, **(shape_params or {}))
        elif kind == "file":
            if not cad_file or not Path(cad_file).expanduser().is_file():
                return {"error": f"cad_file {cad_file!r} does not exist"}
            request.update(path=str(Path(cad_file).expanduser().resolve()), dim=dim)
        else:
            return {"error": "kind must be polygon, shape or file"}
        try:
            dst = _new_mdpa_path(output, overwrite)
        except ValueError as exc:
            return {"error": str(exc)}

        def work():
            return mesh_io.gmsh_generate(request, timeout=timeout, element_name=element_name,
                                         condition_name=condition_name, property_id=property_id)

        try:
            mesh, info = await anyio.to_thread.run_sync(work)
        except mesh_io.GmshUnavailable as exc:
            return {"error": GMSH_HINT, "details": str(exc)[-500:]}
        except (RuntimeError, ValueError) as exc:
            return {"error": str(exc)}
        return {**_finish(mesh, dst, None), "gmsh": info}
