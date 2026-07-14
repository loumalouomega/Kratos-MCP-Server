# Meshes (MDPA)

Generate, inspect and validate Kratos `.mdpa` meshes. All of this is pure
Python — no Kratos needed — except deep validation. See the
[MDPA format guide](/guide/mdpa-format) for the file format itself.

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
