# Meshes (MDPA)

Generate, inspect and validate Kratos `.mdpa` meshes. All of this is pure
Python — no Kratos needed — except deep validation. See the
[MDPA format guide](/guide/mdpa-format) for the file format itself.

To change a mesh (nodes, entities, parts, transforms, boundary conditions)
see [Mesh editing](/tools/mesh-editing); for other shapes and file formats see
[Geometry & mesh formats](/tools/geometry).

Only `mdpa_create_structured_mesh` is limited to line/rectangle/box grids —
`mdpa_inspect`/`mdpa_validate`/`mdpa_get_nodes` work on **any** valid
`.mdpa` file, including externally-authored unstructured meshes with curved
boundaries (e.g. the [NACA airfoil example](/tutorials/naca-airfoil)'s real
~21k-node GiD mesh) that this server has no way to generate itself.

## mdpa_create_structured_mesh

Generate a structured mesh with named boundary submodelparts and write it as
`.mdpa`.

| Parameter | Type | Description |
| --- | --- | --- |
| `path` | string | output file (`.mdpa` appended if missing) |
| `kind` | string | `line`, `rectangle` or `box` |
| `size` | number[] | `[L]` / `[W, H]` / `[Lx, Ly, Lz]` |
| `divisions` | int[] | `[n]` / `[nx, ny]` / `[nx, ny, nz]` |
| `element_name` | string? | Kratos element type (defaults below) |
| `condition_name` | string? | boundary condition type (defaults below) |
| `triangles` | bool | split rectangle quads into triangles |

Defaults: rectangle → `SmallDisplacementElement2D4N` +
`LineLoadCondition2D2N`; box → `SmallDisplacementElement3D8N` +
`SurfaceLoadCondition3D4N`; line → `TrussLinearElement2D2N`, no conditions.

Boundary submodelparts (each with nodes **and** conditions, ready for both
Dirichlet fixes and surface loads):

- `line`: `start`, `end` — `rectangle`: `left`, `right`, `bottom`, `top` —
  `box`: `xmin`, `xmax`, `ymin`, `ymax`, `zmin`, `zmax`
- plus `domain` with every node and element (target materials here).

```json
// mdpa_create_structured_mesh("/case/mesh.mdpa", "rectangle", [1.0, 0.2], [20, 4])
{
  "written_to": "/case/mesh.mdpa",
  "num_nodes": 105, "num_elements": 80, "num_conditions": 48,
  "sub_model_parts": { "domain": {"nodes": 105, "...": 0}, "left": {"nodes": 5, "conditions": 4}, "...": {} }
}
```

::: tip Thermal / fluid meshes
Use generic simplex elements — `element_name: "Element2D3N"`,
`triangles: true`, and `condition_name: "ThermalFace2D2N"` (thermal); the
solvers replace them with the physical element at import time.
:::

## mesh_remesh

*Needs Kratos* with `KratosMeshingApplication` built with MMG (the pip wheel bundles
it: `kratos_install(applications=["MeshingApplication"])`). Adaptively remesh a
`.mdpa` with MMG and write a new one; the input is never modified. The
[adaptive remeshing tutorial](/tutorials/adaptive-remeshing) walks through verified numbers.

| Parameter | Type | Description |
| --- | --- | --- |
| `input_mdpa`, `output_mdpa` | string | source mesh and the file to write (`.mdpa` appended; must differ). An existing output is only replaced with `overwrite: true` |
| `strategy` | string | `levelset` (default), `hessian` or `uniform` |
| `region` | object? | `levelset`: `{"type": "sphere", "center": [x, y], "radius": r}` or `{"type": "box", "min": [...], "max": [...]}`; optional `"mode"`: `inside` (default — the whole region gets `min_size`) or `surface` (only a layer around its boundary) |
| `distance_expression` | string? | `levelset` alternative to `region`: a signed distance in `x`, `y`, `z` |
| `field_variable`, `field_expression` | string? | `hessian`: the Kratos scalar variable (e.g. `TEMPERATURE`) and the analytic field assigned to it on every node before remeshing |
| `field_results`, `field_series`, `field_time`, `field_step` | string?, string?, number?, int? | `hessian` alternative to `field_expression`: drive the metric from a **solved** field. `field_results` is a job's `result-index.json` (record picked by `field_time`, exact, or `field_step`; default the last) or one ASCII `.vtk`/`.vtu` file with `field_variable` as point data on exactly this mesh's nodes (matched by coordinates; Kratos `binary` VTK is not readable — use `file_format: "ascii"`). Give one of `field_expression` / `field_results` |
| `min_size`, `max_size` | number? | element size bounds. Defaults: `max_size` = mean input element size, `min_size` = `max_size / 4`. `uniform` takes one target size in `min_size` |
| `transition_distance` | number? | `levelset`: distance over which the size grows from `min_size` to `max_size` (default `3 * max_size`) |
| `interpolation_error` | number? | `hessian`: target P1 interpolation error (default 0.04); smaller means finer |
| `passes` | int | repeat remeshing on the previous result, 1–6 (default 1). A metric computed on a mesh too coarse for the feature under-resolves it |
| `preserve_sub_model_parts` | string[]? | parts checked for lost conditions and moved bounding box (default: every part with conditions) |
| `freeze_sub_model_parts` | string[]? | parts whose nodes MMG may not touch (`fix_contour_model_parts`) |
| `check_fields` | object[]? | `[{"variable": "TEMPERATURE", "expression": "x + 2*y"}]`: assigned before remeshing, compared with the exact expression on the new nodes |
| `applications` | string[]? | applications to import before reading (default: StructuralMechanics, ConvectionDiffusion, FluidDynamics — element names must be registered) |

Expressions use `x`, `y`, `z`, `pi`, `e`, `+ - * / % **` (constant exponent, |n| ≤ 16) and
`sqrt sin cos tan exp log tanh atan abs min max`; anything else is rejected before
anything runs. Elements must be simplices (triangles or tetrahedra); the element and
condition types of the input are kept.

```json
// mesh_remesh("/case/plate.mdpa", "/case/adapted.mdpa", strategy="hessian",
//             field_variable="TEMPERATURE", field_expression="tanh(30*(x-0.5))",
//             min_size=0.005, max_size=0.25, passes=3)   (excerpt)
{
  "written_to": "/case/adapted.mdpa",
  "passes": [{"pass": 1, "nodes": 162, "elements": 286},
             {"pass": 2, "nodes": 269, "elements": 496},
             {"pass": 3, "nodes": 385, "elements": 724}],
  "before": {"num_elements": 200, "element_size": {"min": 0.114, "mean": 0.114, "max": 0.114}},
  "after":  {"num_elements": 724, "element_size": {"min": 0.0208, "mean": 0.0503, "max": 0.158}},
  "change": {"nodes": 264, "elements": 524, "conditions": 4, "element_count_ratio": 3.62},
  "preserved": {"left": {"ok": true, "before": {"nodes": 11, "conditions": 10}, "after": {"nodes": 7, "conditions": 6}}},
  "transfer": {},
  "issues": []
}
```

`preserved.<part>.ok` is false (with `problems`) when a part loses its conditions or
nodes, vanishes, or its node bounding box moves. MMG keeps straight boundaries and
corners exact but is free to coarsen or refine the boundary discretization (above, `left`
goes from 11 to 7 nodes where the field is smooth) — `freeze_sub_model_parts` forbids that. `transfer.<variable>` gives
`max_abs_error`, `mean_abs_error` and `value_range` of the interpolated field. For a
solved field (`field_results`) there is no exact expression, so it reports
`{"source": "field", "value_range_before", "value_range_after"}` instead — MMG's
interpolation can overshoot a sharp front, so compare the two ranges.

A field read from results (and any scalar `Begin NodalData` blocks of the input mesh) is
transferred to the new nodes and **written to the output `.mdpa` as `Begin NodalData`**;
`fields_written` lists the variable names, and the field is carried from one pass to the
next. Analytic `field_expression` / `check_fields` values are not written. Vector
`NodalData` blocks are skipped by the server's `.mdpa` parser.

Errors (`{"error": ...}`): missing/identical/existing files, invalid input mesh,
bad arguments, MMG not available (`meshing_support` explains what the build lacks).

## mdpa_inspect

Report the contents of an `.mdpa` file: counts by entity type, bounding box,
property ids and the full submodelpart tree with per-part entity counts.

| Parameter | Type | Description |
| --- | --- | --- |
| `path` | string | mesh file |

## mdpa_validate

Lint a mesh: dangling node/element/condition references, empty
submodelparts, parse errors.

| Parameter | Type | Description |
| --- | --- | --- |
| `path` | string | mesh file |
| `deep` | bool | also round-trip through the real Kratos `ModelPartIO` (needs Kratos; catches unregistered element names, malformed blocks) |

**Returns**: `{valid, issues: [...], kratos_read?: {...}}`.

## mdpa_get_nodes

Return node ids and coordinates — useful for choosing probe points or
checking where a boundary region actually is.

| Parameter | Type | Description |
| --- | --- | --- |
| `path` | string | mesh file |
| `sub_model_part` | string? | dotted path, e.g. `right` or `outer.inner` |
| `node_ids` | int[]? | explicit selection |
| `limit` | int | max nodes returned (default 100; `truncated` flags overflow) |
