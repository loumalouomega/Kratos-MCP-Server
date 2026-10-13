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
| `polygon` | `vertices: [[x, y], ...], refinements=0, holes?, target_size?, size_field?, size_sources?, max_elements=200000, smooth_iterations=0` (ear clipping with hole bridging + midpoint subdivision; `holes` is a list of strictly interior vertex loops; `target_size` adds subdivision rounds until no edge is longer than it, at most 6 rounds total; the grading parameters are described below) | `side_0`, `side_1`, ... plus `hole_<k>` per hole |

`element_name` overrides the default `SmallDisplacementElement…` type. For
CAD input or quality-driven meshes use gmsh.

### Graded polygon meshes (no gmsh)

`size_field` is an expression of `x` and `y` (the same safe subset as the
selectors: arithmetic plus `sqrt sin cos tan exp log tanh atan abs min max`,
e.g. `"0.1 + 0.3*sqrt((x-1)**2 + y**2)"`). `size_sources` is a list of
`{"point": [x, y], "size": 0.05, "growth": 0.4}` or
`{"segment": [[x0, y0], [x1, y1]], "size": …, "growth": …}` giving
`size + growth * distance`; `growth` defaults to 0.3. The smallest of all given
sizes wins. After the optional uniform `target_size` rounds the mesh is refined
by conforming longest-edge (Rivara) bisection — no hanging nodes, boundary
nodes stay on their original segments, hole slits are handled — until every
triangle's longest edge is at most the local size (the smallest of the size at
its corners and centroid). `max_elements` stops runaway sizes with an error;
`smooth_iterations` (0–100) Laplacian-smooths interior nodes without ever
flipping a triangle. Triangle shape is that of ear clipping plus bisection
(aspect ratios of 2–4 are typical); use gmsh when quality matters more.

## mesh_import / mesh_export

`mesh_import(source, output, element_name?, condition_name?, property_id?,
file_format?)` converts anything meshio reads (gmsh `.msh`, `.vtk`/`.vtu`,
MED, Abaqus `.inp`, Nastran, XDMF, …) into `.mdpa`: the highest-dimension
cells become elements, the next lower dimension conditions, and named physical
groups / cell sets become sub model parts (a `domain` part with everything is
added). Linear and quadratic line, triangle, quad, tetra, hexahedron, wedge and
pyramid cells are converted (quadratic Kratos ordering matches meshio/VTK),
plus the cubic `line4`, `triangle10` and `quad16`; 15-node wedges and 13-node
pyramids cannot be represented by meshio itself and other cubic/higher
Lagrange cells are rejected). The default type names are generic (`Element2D3N` …
`SurfaceCondition3D4N`, with `…2D6N` / `…3D10N` / … variants for quadratic
cells); set `element_name` for a specific solver, e.g.
`SmallDisplacementElement2D3N`.

`mesh_export(path, output, file_format?, overwrite?, linearize_unsupported?)` goes the other way. Elements come
first, then conditions; cell data holds `kratos:id`, `kratos:property_id` and
`kratos:entity` (0 element / 1 condition), sub model parts become cell sets
where the format supports them, scalar nodal data becomes point data (vector
nodal data too, when its `[d](…)` value parses). 15-node wedges cannot be
exported through meshio (nor 13-node pyramids): linearize them first
(`mdpa_convert_cells` `quadratic_to_linear`), pass `linearize_unsupported=true`
to write only their corner nodes (a `warnings` entry says so) or keep the
`.mdpa`. Cubic triangles/quads/lines only export to formats that have them
(gmsh `.msh`, written in Gmsh format; VTK is refused).

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
