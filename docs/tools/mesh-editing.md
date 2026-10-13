# Mesh editing (MDPA)

Build and modify `.mdpa` meshes node by node, entity by entity: add, move and
delete nodes; add, delete and retype elements, conditions and geometries;
create and edit sub model parts; transform, merge, extrude and convert cells;
create boundary conditions on free faces; check mesh quality; read and write
the data blocks (`Tables`, `ElementalData`, `ConditionalData`, vector/flag
`NodalData`, per-part data). Everything is pure Python — no Kratos build needed.

## How the tools behave

- Each editing tool reads a `.mdpa`, applies **one** operation and writes the
  result: **in place** by default, or to `output` (an existing `output` is only
  replaced with `overwrite=true`). `mdpa_merge`, `mdpa_extract` and
  `mdpa_extrude` always need an `output`.
- The result is `{written_to, in_place, result, mesh, valid, issues?}`:
  the operation summary, the new `mdpa_inspect`-style summary, and the
  validation issues of the edited mesh (it is written even when issues remain,
  so you can build a mesh in several steps).
- Data blocks round-trip through every tool: `Tables`, `ElementalData`,
  `ConditionalData`, scalar/vector/flag `NodalData` and per-`SubModelPart`
  data/table/property references are preserved, and their ids follow
  renumber/merge/delete/extract. A file with blocks the editor cannot represent
  (`Constraints`, …) is **refused for in-place edits** — pass `output=` to
  write a copy; the dropped blocks are named in `warnings`.
- `Begin Geometries` and `SubModelPartGeometries` blocks are read, edited and
  written.
- Quadratic cells (6-node triangles, 8/9-node quads, 10-node tets, 20/27-node
  hexes, 15-node prisms, 3-node lines) are first-class: quality, boundary
  extraction, extrusion, cell conversion and meshio import/export handle them
  (measures use the corner nodes). So are the cubic 4-node line, 10-node
  triangle (`…2D10N`) and 16-node quadrilateral (two nodes per edge, listed in
  the edge direction, then the interior nodes) and 5/13/14-node pyramids.
  Their node order was checked against the Kratos geometry sources
  (`Line2D4`, `Triangle2D10`, `Pyramid3D13`); Kratos has no 16-node quad or
  14-node pyramid, which exist here only for meshio interchange. Cubic cells
  cannot be extruded (convert with `quadratic_to_linear` first). Cubic
  tetrahedra and 18-node wedges, which Kratos does not have either, and
  everything beyond cubic, are rejected with a message; a bare 10-node
  entity is a quadratic tetrahedron unless its name says `2D10`/`Tri`.
- Errors come back as `{"error": "..."}`.

## Selectors

Tools that act on a set of nodes take either `node_ids` or a `selector` — an
intersection of any of:

| Key | Meaning |
| --- | --- |
| `ids` | explicit node ids |
| `part` | members of a sub model part (dotted path) |
| `box` | `{min: [x,y,z], max: [x,y,z]}` |
| `sphere` | `{center: [...], radius: r}` |
| `plane` | `{point: [...], normal: [...]}` — nodes within `tolerance` of the plane |
| `expression` | nodes where an expression in `x`, `y`, `z` is `<= 0` (a signed distance, e.g. `x*x + y*y - 1`) |
| `tolerance` | default 1e-9 × bounding-box diagonal |
| `all` | every node |

Use `mdpa_select_nodes` to preview a selector.

## Nodes

| Tool | What it does |
| --- | --- |
| `mdpa_add_nodes(path, coordinates, ids?, sub_model_part?)` | add `[x, y(, z)]` nodes; next free ids unless given |
| `mdpa_move_nodes(path, node_ids\|selector, coordinates\|offset)` | absolute positions (one per node) or a shift |
| `mdpa_delete_nodes(path, node_ids\|selector, cascade?)` | refused while entities use the nodes, unless `cascade=true` deletes those entities too |
| `mdpa_select_nodes(path, selector, limit?)` | read-only: count, ids and bounding box of a selection |
| `mdpa_set_nodal_data(path, variable, value, node_ids\|selector, fixed?)` | scalar (`float`) or vector (`"[3](…)"`) `Begin NodalData` values, optionally fixed |
| `mdpa_get_data_blocks(path)` | read-only: Tables, ElementalData/ConditionalData, vector/flag NodalData and per-part data references |
| `mdpa_set_elemental_data(path, kind, variable, values?, remove_ids?)` | upsert/delete `Begin ElementalData` (`elemental`) or `Begin ConditionalData` (`conditional`) rows (numbers, lists or `"[3](…)"` strings) |
| `mdpa_set_table(path, table_id, x_variable, y_variable, rows)` / `mdpa_delete_table(path, table_id)` | create/replace/delete a top-level `Begin Table` block (delete refused while referenced) |
| `mdpa_set_sub_model_part_data(path, name, data?, tables?, properties?)` | replace a part's `SubModelPartData` / tables / properties references wholesale |
| `mdpa_transform(path, operation, ...)` | `translate` (`vector`), `rotate` (`angle`°, `axis`, `origin`), `scale` (`factors`, `origin`), `mirror` (`point`, `normal`); all nodes unless limited. Mirror and negative scale re-order the connectivity so cells keep a positive measure |

## Elements, conditions, geometries

| Tool | What it does |
| --- | --- |
| `mdpa_get_entities(path, kind, ids?, entity_type?, sub_model_part?, limit?)` | list entities with type, property id and connectivity |
| `mdpa_add_entities(path, kind, entity_type, entities, sub_model_part?)` | add `{nodes, id?, property_id?}` items of one type; the `...2D3N` / `3D8N` suffix fixes the node count |
| `mdpa_delete_entities(path, kind, ids \| sub_model_part)` | remove entities (from every sub model part too) |
| `mdpa_set_entity_properties(path, kind, ids \| sub_model_part, property_id?, new_type?)` | switch the Materials entry or retype (same node count) |

`kind` is `element`, `condition` or `geometry`.

## Sub model parts

| Tool | What it does |
| --- | --- |
| `mdpa_create_sub_model_part(path, name, node_ids\|selector?, element_ids?, condition_ids?, geometry_ids?, include?, exist_ok?)` | `name` may be dotted (`domain.fixed`) to nest; `include=['condition']` also takes the conditions lying entirely on the part's nodes |
| `mdpa_edit_sub_model_part(path, name, action, ...)` | `add` / `remove` members, `rename` (`new_name`), `delete` |

Adding members also adds them to the ancestors (as Kratos does) and adds the
nodes of added entities.

## Whole-mesh operations

| Tool | What it does |
| --- | --- |
| `mdpa_merge(path, other, output, merge_tolerance?)` | append a mesh (ids shifted, same-named parts united with their data references; colliding table ids shifted; data blocks carried over); `merge_tolerance` merges coincident nodes so two transformed blocks become one conforming mesh |
| `mdpa_renumber(path, start?)` | consecutive ids; every reference (including data blocks) follows |
| `mdpa_extract(path, sub_model_part, output)` | one part (with children, the nodes it uses, its data references and the tables they need) as a new mesh |
| `mdpa_extrude(path, output, heights, z0?, element_names?, condition_names?, cap_condition_names?)` | planar mesh → 3D: triangles → prisms, quads → hexahedra, line conditions → surface quads; quadratic tri6 → 15-node wedges, quad8 → 20-node hexes, 3-node lines → 8-node quads. Names map `2DnN` → `3DmN` unless overridden; parts `zmin` / `zmax` hold the caps (cap keys `"3"`/`"4"`, `"6"`/`"8"` for quadratic caps). Tables, element/condition data (replicated per layer) and nodal data (interpolated) are carried over |
| `mdpa_convert_cells(path, mode, element_name?, condition_name?)` | `quad_to_tri`, `hex_to_tet` (Kuhn split: conforming on structured meshes with consistent node order; boundary quads follow the tet faces), or `quadratic_to_linear` (drop mid-edge/face/interior nodes of quadratic and cubic cells, ids unchanged) |
| `mdpa_extract_boundary(path, condition_name, sub_model_part, element_part?, selector?, property_id?)` | conditions on the free faces (linear, quadratic and cubic line, tri, quad, tet, prism, pyramid and hex cells; higher-order faces carry their mid nodes, so the name map needs `"3"`/`"4"`/`"6"`/`"8"`/`"9"` keys by face node count — `"4"` is both a quad and a cubic edge, as 2D and 3D meshes do not mix); `selector` keeps only faces whose nodes are all selected, e.g. a plane |
| `mdpa_set_properties(path, property_id?, values?, remove?, model_part_data?)` | the mesh-embedded `Properties` block and the `ModelPartData` header |
| `mdpa_quality(path, worst?)` | per type: measure min/mean/max/total, aspect ratio with the worst ids, corner-angle extremes in degrees (`angle_degrees`: min, max and the cells with the smallest angle; triangles and quads), and inverted or degenerate cells (`ok: false`) |

::: warning Pressure sign
Conditions from `mdpa_extract_boundary` and the generators run with the
domain on their **left** (outward normal on the right). On such conditions a
positive `POSITIVE_FACE_PRESSURE` acts **along the outward normal** (a
tension); a compressive pressure needs a *negative* value. This was verified
on a plate and on a thick cylinder against the Lamé solution.
:::

## Example: a load surface on a plain mesh

```text
// any mesh, with or without conditions (a rectangle here)
mdpa_create_structured_mesh("/case/mesh.mdpa", "rectangle", [1.0, 0.2], [20, 4])
mdpa_create_sub_model_part("/case/mesh.mdpa", "fixed",
    selector={"plane": {"point": [0,0,0], "normal": [1,0,0]}})
mdpa_extract_boundary("/case/mesh.mdpa", "LineLoadCondition2D2N", "tip",
    selector={"plane": {"point": [1,0,0], "normal": [1,0,0]}})
mdpa_quality("/case/mesh.mdpa")                           // ok: true
```

The [`test_edit_integration.py`](https://github.com/loumalouomega/Kratos-MCP-Server/blob/master/tests/test_edit_integration.py)
tests run cases built this way: a cantilever from a plain mesh (tip deflection
within 20 % of beam theory), a quarter thick cylinder under internal pressure
(radial displacement within 3 % of Lamé at the inner and outer radius) and a
plate extruded to hexahedra and split into tetrahedra (0.88 × and 0.52 × the
beam-theory deflection on a coarse mesh — linear tetrahedra lock in bending).
