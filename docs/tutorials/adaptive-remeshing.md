# Tutorial: adaptive remeshing with MMG

A uniform mesh spends elements where nothing happens. Kratos' MeshingApplication wraps
the MMG library to remesh a model part to a size field: small elements where a feature
needs them, large ones elsewhere, boundaries kept. This tutorial uses the two ways this
server exposes it — `mesh_remesh` (an `.mdpa` in, an `.mdpa` out) and
`add_remeshing_process` (the case remeshes itself at setup) — on the worked example
`kratos://examples/adaptive-remeshing`. Every number below comes from a real run against
Kratos 10.4.4 with `KratosMeshingApplication` (the pip wheel bundles MMG).

## Install

```json
// kratos_install(applications=["MeshingApplication"])
```

`validate_case(deep=true)` and `mesh_remesh` check `meshing_support` and say what is
missing when MMG is not compiled in. Elements must be linear simplices (triangles or
tetrahedra) — the structured generators have `triangles: true` for rectangles; `mesh_remesh`
refuses anything else, and mixed triangle/tetrahedron meshes, up front.

## 1. Refine around a region

`plate.mdpa` is a unit square, 10 × 10 cells split into 200 triangles with boundary parts
`left/right/bottom/top`. Refine inside a disk and let the size grow back to the far field:

```json
// mesh_remesh("plate.mdpa", "local.mdpa",
//             region={"type": "sphere", "center": [0.5, 0.5], "radius": 0.15},
//             min_size=0.02, max_size=0.1, transition_distance=0.25,
//             check_fields=[{"variable": "TEMPERATURE", "expression": "x + 2*y"}])
```

The 200 triangles become 872 (121 → 457 nodes); elements near the disk are less than half
the size of the ones in the far corner. The result carries:

- `preserved`: for every boundary part, `ok: true` — conditions and node bounding box
  survived. MMG keeps straight boundaries and corners exact but may change how many
  nodes discretize them; `freeze_sub_model_parts=["left", ...]` pins those nodes instead
  (a uniform remesh at 0.05 refines `left` from 11 to 21 nodes, frozen it stays at 11).
- `transfer.TEMPERATURE.max_abs_error`: about `1e-15`. `check_fields` assigns the exact
  field on the *old* nodes, MMG interpolates to the new ones, and the report compares with
  the exact expression there — linear fields come through to round-off.
- `change`/`before`/`after`: counts, element size min/mean/max, and `issues` (the output is
  linted with the same checks as `mdpa_validate`).

`region` can also be a `box`; `"mode": "surface"` refines only a layer around the region's
boundary (an interface), and `distance_expression` accepts any signed distance in `x`, `y`,
`z`.

## 2. Adapt to a field: hessian, repeated

For a sharp front, say `tanh(30*(x - 0.5))` (width ≈ 0.03, three times finer than the 0.11
elements of `plate.mdpa`), let the metric follow the field's second derivatives:

```json
// the contents of request.json
{"input_mdpa": "plate.mdpa", "output_mdpa": "plate_adapted.mdpa", "strategy": "hessian",
 "field_variable": "TEMPERATURE", "field_expression": "tanh(30*(x-0.5))",
 "min_size": 0.005, "max_size": 0.25, "passes": 3}
```

| pass | nodes | elements |
| ---: | ---: | ---: |
| 1 | 162 | 286 |
| 2 | 269 | 496 |
| 3 | 385 | 724 |

One pass is not enough: the Hessian of a front narrower than the elements is computed on a
mesh that cannot see it, and the first adapted mesh is no more accurate than the input.
Each further pass re-evaluates the field on the new, finer mesh and adapts again. Measured
as the largest error of the piecewise-linear interpolant of the field on a 201 × 201
sampling grid:

| mesh | elements | max interpolation error |
| --- | ---: | ---: |
| `plate.mdpa` | 200 | 0.436 |
| hessian, 1 pass | 286 | 0.43 |
| **hessian, 3 passes** | **724** | **0.126** |
| uniform, `min_size=0.065` | 657 | 0.237 |
| uniform, `min_size=0.055` | 796 | 0.240 |

At the same element count the adapted mesh is about twice as accurate. (`interpolation_error`,
default 0.04, scales the whole hessian metric: 0.01 reaches an error of 0.05 in three passes
with 2961 elements.) MMG's counts can differ by a few elements between builds and thread
counts; the repository tests assert ratios, not these exact numbers.

The metric field is *analytic* here: `mesh_remesh` evaluates the expression on the nodes
itself. To adapt to a **solved** field instead, see the next section.

## 2b. Remesh to a solved field

Run the case with `file_format: "ascii"` in its `vtk_output_process` (meshio cannot read
Kratos' `binary` VTK), then pass the job's result index:

```python
mesh_remesh("/case/mesh.mdpa", "/case/adapted.mdpa", strategy="hessian",
            field_variable="TEMPERATURE", field_results="/case/result-index.json",
            min_size=0.01, max_size=0.1, passes=2)
```

The VTK points are matched to the mesh nodes by coordinates (every point must match exactly
one node, so export the root model part of this mesh). The thermal case of section 3 (231
nodes, 400 triangles) solved to t = 0.3 and remeshed this way (Kratos 10.4.4) gives
1648 elements after one pass and 966 / 1030 after passes 2 / 3 with `passes=3`; the
transferred `TEMPERATURE` is written to `adapted.mdpa` as a `Begin NodalData TEMPERATURE`
block, together with the input mesh's own `DISTANCE` block (`fields_written`). MMG's
interpolation of a very sharp front undershoots (`value_range_after[0]` is about -3.5 to
-4.7 against -0.03 in the solved field), which `transfer.TEMPERATURE` reports; the Dirichlet
side keeps 100. The repository test asserts the structure of the result, not these counts.

## 3. Remesh at setup, inside a case

`setup_remesh/` is the thermal transient case of the other tutorials with one difference:
the mesh has a `Begin NodalData DISTANCE` block (distance to a disk of radius 0.1 at
(0.5, 0.25), clamped at 0 inside) and the solver lists `DISTANCE` / `DISTANCE_GRADIENT` in
`auxiliary_variables_list` so the variables exist. `add_remeshing_process` appended (abridged):

```json
{"python_module": "mmg_process", "kratos_module": "KratosMultiphysics.MeshingApplication",
 "process_name": "MmgProcess",
 "Parameters": {"model_part_name": "ThermalModelPart", "strategy": "levelset",
                "initial_remeshing": true, "minimal_size": 0.02, "force_min": true,
                "maximal_size": 0.1, "force_max": true,
                "level_set_strategy_parameters": {"scalar_variable": "DISTANCE",
                                                  "gradient_variable": "DISTANCE_GRADIENT"},
                "sizing_parameters": {"reference_variable_name": "DISTANCE"}}}
```

`run_simulation("setup_remesh")` runs in about a second: the 231-node, 400-element mesh is
remeshed before the solver builds its strategy and the VTK output has **1608 nodes**
(3078 triangles) concentrated around the disk; `TEMPERATURE` still reaches 100 on the
Dirichlet side.

## Limits worth knowing

- **Only at setup.** `mmg_process` can also fire inside the solution loop
  (`step_frequency`), but a standard solver keeps its strategy bound to the old mesh:
  the thermal run above printed `MMG Remeshing Process: Remesh finished` and then died with
  SIGSEGV (`returncode: -11`). `add_remeshing_process` does not offer it and `validate_case`
  warns about hand-written blocks that do.
- **The driving field must be on the nodes at setup.** Processes that assign values (such as
  `assign_scalar_variable_process`) run later, in `ExecuteBeforeSolutionLoop`; use a
  `NodalData` block, or run `mesh_remesh` offline and use its output as the case mesh.
- **Remeshing renumbers nodes and elements.** Studies, checkpoints/resume, `mpi_ranks` and
  interactive sessions reject cases with an `mmg_process`; the Flowgraph round trip keeps
  it as an ordinary process.
- **2D has been verified; 3D has not.** The 3D and surface paths of `MmgProcess` are wired
  (the result's `mesh_kind` says which one a mesh takes, and `meshing_support` reports
  `mmg2d` / `mmg3d` / `mmg3d_surfaces` separately) but have not been run against reference
  numbers; those results carry a warning.
- Only scalar fields from results (and scalar `NodalData` of the input) are written to the
  output `.mdpa`; analytic `field_expression` / `check_fields` values are not. MMG's
  interpolation can over/undershoot a sharp front.
- **`strategy: spr` (error estimated from recovered stresses) is not offered**: it needs
  element stress data from a live solved model part, which a mesh plus VTK point data
  cannot provide.
