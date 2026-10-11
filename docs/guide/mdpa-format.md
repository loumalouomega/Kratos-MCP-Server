# The MDPA mesh format

Kratos reads meshes from plain-text `.mdpa` files. Every section is a block
delimited by `Begin <Name> [args]` / `End <Name>`; `//` starts a comment.
The file is referenced from ProjectParameters.json **without** its extension
(`"input_filename": "mesh"` → `mesh.mdpa`).

## A complete small example

```text
Begin ModelPartData
End ModelPartData

Begin Properties 1
End Properties

Begin Nodes                       //  id   x     y    z
    1  0.0  0.0  0.0
    2  1.0  0.0  0.0
    3  1.0  0.5  0.0
    4  0.0  0.5  0.0
End Nodes

Begin Elements SmallDisplacementElement2D4N   //  id  prop  connectivity
    1  1  1 2 3 4
End Elements

Begin Conditions LineLoadCondition2D2N
    1  1  2 3
End Conditions

Begin SubModelPart domain
    Begin SubModelPartNodes
        1
        2
        3
        4
    End SubModelPartNodes
    Begin SubModelPartElements
        1
    End SubModelPartElements
    Begin SubModelPartConditions
    End SubModelPartConditions
End SubModelPart

Begin SubModelPart right
    Begin SubModelPartNodes
        2
        3
    End SubModelPartNodes
    Begin SubModelPartElements
    End SubModelPartElements
    Begin SubModelPartConditions
        1
    End SubModelPartConditions
End SubModelPart
```

## Blocks

| Block | Content |
| --- | --- |
| `ModelPartData` | global key–value data, usually empty |
| `Properties <id>` | one per material id; values normally come from Materials.json instead |
| `Nodes` | `id x y z`, one per line |
| `Elements <TypeName>` | `id property_id node_ids...` |
| `Conditions <TypeName>` | same layout; boundary entities for loads/fluxes |
| `Geometries <TypeName>` | `id node_ids...` (no property id); read, edited and written, also as `SubModelPartGeometries` |
| `NodalData <VARIABLE>` | `id fixed value`; scalar rows keep their fixed flag, vector rows (`id fixed [3](x, y, z)`) are kept verbatim, flag blocks (bare ids) are kept |
| `Table <id> <X> <Y>` | `x y` rows of a piecewise-linear table; read, edited and written |
| `ElementalData <VARIABLE>` / `ConditionalData <VARIABLE>` | `id value` rows (scalars and `[n](…)` vectors kept verbatim); follow renumber/merge/delete |
| `SubModelPart <name>` | named entity sets, may be nested; how processes target regions. May carry `SubModelPartData` key-values plus `SubModelPartTables` / `SubModelPartProperties` references, all preserved |

::: warning Blocks that are not kept
`Constraints`, `CommunicatorData` and other blocks the parser does not model
are skipped when a file is read (`mdpa_inspect` works regardless). The
[mesh editing](/tools/mesh-editing) tools therefore refuse to overwrite such
a file in place and name the skipped blocks.
:::

## Naming conventions

Element and condition type names encode dimension and node count:
`SmallDisplacementElement3D8N` is a 3D, 8-node hexahedron;
`LineLoadCondition2D2N` a 2-node line condition in 2D. Quadratic cells
(`…2D6N` triangles, `…2D8N` quads, `…3D10N` tetrahedra, `…3D20N` hexahedra,
`…3D15N` prisms, `…2D3N`/`…3D3N` lines) list their corner nodes first in Kratos
ordering, then the mid-edge (and face) nodes; the mesh tools use the corners
for measures and preserve the full connectivity.

Two conventions matter in practice:

::: warning Load-bearing vs geometric conditions
`LineCondition2D2N` / `SurfaceCondition3D4N` are *geometric* conditions —
they contribute nothing to the system. To apply line or surface loads you
need `LineLoadCondition2D2N` / `SurfaceLoadCondition3D4N` (structural), or
`ThermalFace2D2N` / `FluxCondition2D2N` (thermal). A mesh with geometric
conditions plus a load process runs without errors and produces exactly zero
displacement.
:::

::: tip Generic elements for thermal and fluid
The convection-diffusion and fluid solvers **replace** mesh elements at
import time (`element_replace_settings` / `formulation`). Thermal and fluid
meshes therefore use the generic `Element2D3N` / `Element3D4N`, and the
solver substitutes the physics. The replacement machinery requires simplex
meshes (triangles/tetrahedra).
:::

## SubModelParts and processes

A process in ProjectParameters.json targets a submodelpart by dotted path
rooted at the solver's `model_part_name`:

```json
{ "model_part_name": "Structure.right", "variable_name": "LINE_LOAD", ... }
```

- Dirichlet-style processes (fix displacement/temperature) need the
  **nodes** of the region.
- Load/flux processes applied "to conditions" need **conditions** in the
  region.

The mesh generator (`mdpa_create_structured_mesh`) creates both: every
boundary part carries its nodes and a set of boundary conditions.

## Generated meshes

`mdpa_create_structured_mesh` writes ready-to-use meshes:

| kind | size / divisions | boundary submodelparts |
| --- | --- | --- |
| `line` | `[L]` / `[n]` | `start`, `end` |
| `rectangle` | `[W, H]` / `[nx, ny]` | `left`, `right`, `bottom`, `top` |
| `box` | `[Lx, Ly, Lz]` / `[nx, ny, nz]` | `xmin`, `xmax`, `ymin`, `ymax`, `zmin`, `zmax` |

All variants include a `domain` part containing every node and element —
the natural target for materials (`Structure.domain`) and volume loads.
