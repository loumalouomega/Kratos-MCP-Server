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
| `SubModelPart <name>` | named entity sets, may be nested; how processes target regions |

## Naming conventions

Element and condition type names encode dimension and node count:
`SmallDisplacementElement3D8N` is a 3D, 8-node hexahedron;
`LineLoadCondition2D2N` a 2-node line condition in 2D.

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
