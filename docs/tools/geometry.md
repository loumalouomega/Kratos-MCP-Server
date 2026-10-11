# Geometry & mesh interchange

Generate meshes of parametric shapes, convert from and to other mesh formats,
and (optionally) mesh arbitrary geometry with gmsh. Pure Python except the
gmsh route, which runs in its own subprocess.

## mdpa_create_geometry_mesh

Write a mesh of a parametric shape. Every mesh has a `domain` part plus named
boundary parts carrying conditions of `condition_name` (default
`LineLoadCondition2D2N` in 2D, `SurfaceLoadCondition3D4N` in 3D), oriented with
the domain on their left. Existing files are only replaced with
`overwrite=true`.

| `kind` | `params` | Parts |
| --- | --- | --- |
| `annulus` | `r_inner, r_outer, n_radial, n_theta, angle=360, triangles=false` | `inner`, `outer` (+ `start`, `end` for a sector) |
| `disc` | `radius, n_radial, n_theta` (triangles) | `boundary` |
| `cylinder` | `radius, height, n_radial, n_theta, n_z, inner_radius?` (prisms, or hexahedra when hollow) | `lateral`, `zmin`, `zmax` |
| `sphere` | `radius, divisions` (radially mapped cube of hexahedra; stretched corner cells) | `surface` |
| `polygon` | `vertices: [[x, y], ...], refinements=0` (ear clipping + midpoint subdivision; no holes) | `side_0`, `side_1`, ... |

`element_name` overrides the default `SmallDisplacementElement…` type. For
holes, graded sizes or CAD input use gmsh.

## mesh_import / mesh_export

`mesh_import(source, output, element_name?, condition_name?, property_id?,
file_format?)` converts anything meshio reads (gmsh `.msh`, `.vtk`/`.vtu`,
MED, Abaqus `.inp`, Nastran, XDMF, …) into `.mdpa`: the highest-dimension
cells become elements, the next lower dimension conditions, and named physical
groups / cell sets become sub model parts (a `domain` part with everything is
added). Only linear line, triangle, quad, tetra, hexahedron and wedge cells are
converted. The default type names are generic (`Element2D3N` …
`SurfaceCondition3D4N`); set `element_name` for a specific solver, e.g.
`SmallDisplacementElement2D3N`.

`mesh_export(path, output, file_format?)` goes the other way. Elements come
first, then conditions; cell data holds `kratos:id`, `kratos:property_id` and
`kratos:entity` (0 element / 1 condition), sub model parts become cell sets
where the format supports them, scalar nodal data becomes point data.

## mesh_generate_gmsh

Needs the optional extra: `uv sync --extra gmsh` (or `pip install gmsh`; on
Linux gmsh also needs `libGLU` and `libXft`). gmsh runs in a **subprocess**
(`gmsh_worker.py`), never inside the server, because it is a large native
library.

| `kind` | Inputs | Result |
| --- | --- | --- |
| `polygon` | `vertices` (counter-clockwise), optional `holes` (vertex loops), `size`, `quads` | parts `domain`, `side_<i>`, `hole_<k>` |
| `shape` | `shape` = `box` (`shape_params: {size_xyz: [lx, ly, lz], origin}`), `cylinder` (`{radius, height}`) or `sphere` (`{radius}`), `size` | tetrahedra; parts `domain`, `boundary` |
| `file` | `cad_file` (`.step`, `.brep`, `.geo`, `.iges`, …), `dim`, `size` | its physical groups, else `domain` / `boundary` |

`size` is the target element size, `size_min` its lower bound. Defaults are
`Element2D3N` / `LineCondition2D2N` (2D) and `Element3D4N` /
`SurfaceCondition3D3N` (3D) unless `element_name` / `condition_name` are
given. Without gmsh the tool answers with an install hint instead of failing.
