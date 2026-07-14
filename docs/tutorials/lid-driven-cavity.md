# Tutorial: lid-driven cavity

The classic incompressible-flow benchmark. A unit square is filled with fluid;
the top wall (the "lid") slides at u = 1 m/s while the other three walls are
no-slip. The shear dragged in by the lid drives one large recirculating vortex.
Density 100 and dynamic viscosity 1 give a Reynolds number Re = ρUL/μ = 100.
Every number below comes from a real run against Kratos 10.4.

You can hand the whole thing to an assistant:

> Set up a lid-driven cavity: a 1×1 m box of fluid (density 100, viscosity 1),
> the top wall moving at 1 m/s, the others no-slip. Run it and show me the
> velocity field.

A smaller, literal, pre-verified version is the `kratos://examples/lid-driven-cavity`
resource, and `notebooks/fluid_cavity.ipynb` walks through this exact case as a
live MCP client.

## 1. Generate the mesh

A coarse 10×10 unit square of triangles (the monolithic VMS solver wants a
simplex mesh). No load conditions are needed — the walls are nodal velocity
constraints.

```json
mdpa_create_structured_mesh({
  "path": "/tmp/cavity/mesh.mdpa",
  "kind": "rectangle", "size": [1.0, 1.0], "divisions": [10, 10],
  "element_name": "Element2D3N", "triangles": true
})
→ { "num_nodes": 121, "num_elements": 200 }
```

The pressure in an all-walls cavity is only defined up to a constant, so one
node must pin it. The structured generator makes `left`/`right`/`bottom`/`top`
sub-model-parts but not a single-node one, so the shipped example adds a
`corner` sub-model-part holding just the lower-left node (see the example files).

## 2. Scaffold the case

The `fluid_transient` template is monolithic Navier–Stokes (VMS). Point it at
the cavity fluid properties:

```json
create_project({
  "directory": "/tmp/cavity", "template": "fluid_transient", "name": "cavity",
  "overrides": { "density": 100.0, "dynamic_viscosity": 1.0,
                 "volume_part": "domain", "skin_parts": ["left","right","bottom","top"] }
})
```

## 3. Apply the walls and the moving lid

No-slip (zero velocity) on three walls, the lid velocity on the top, and the
pressure pin on the corner:

```json
add_boundary_condition({ "parameters_file": ".../ProjectParameters.json",
  "kind": "fix_velocity", "model_part": "FluidModelPart.left", "value": [0,0,0] })
// ...same for FluidModelPart.right and FluidModelPart.bottom...
add_boundary_condition({ "parameters_file": ".../ProjectParameters.json",
  "kind": "inlet_velocity", "model_part": "FluidModelPart.top", "value": [1.0, 0, 0] })
add_boundary_condition({ "parameters_file": ".../ProjectParameters.json",
  "kind": "outlet_pressure", "model_part": "FluidModelPart.corner", "value": 0.0 })
```

## 4. Run

```json
run_simulation({ "case_dir": "/tmp/cavity", "wait_seconds": 60 })
→ { "state": "succeeded", "elapsed_seconds": 1.0 }
```

Thirty pseudo-time steps march the impulsively-started lid to a steady vortex.

## 5. Check the recirculation

Read the velocity on the vertical centerline (x = 0.5). The signature of the
vortex is that the interior velocity *reverses*: fluid dragged forward under the
lid returns backward through the cavity.

```
lid velocity u on the top row .................. 1.00 m/s (imposed)
centerline u(y) at x = 0.5, minimum ............ -0.116 m/s at y = 0.40
```

On a fine (Ghia) mesh at Re = 100 that minimum is about −0.21 at y ≈ 0.46; this
coarse 10×10 mesh under-resolves the vortex core (expected — refine the mesh for
a sharper profile).

## 6. Preview

```json
results_render({ "file": ".../vtk_output/FluidModelPart_0_30.vtk",
                 "variable": "VELOCITY", "camera": "xy", "show_edges": true })
```

Colouring by velocity magnitude from a top-down camera shows the bright
lid-driven shear band along the top and the slow vortex core below.
`results_animate` over the snapshots shows the vortex spinning up from rest.

## Variations

- **Higher Reynolds number** — raise the velocity or lower the viscosity; at
  Re ≳ 1000 secondary corner eddies appear (and you'll want a finer mesh).
- **Finer mesh** — `divisions: [40, 40]` sharpens the centerline profile toward
  the Ghia benchmark.
- **Fractional-step solver** — the `fluid_fractional_step` template solves the
  same case with a cheaper pressure-splitting scheme (see
  `kratos://examples/channel-flow`).
