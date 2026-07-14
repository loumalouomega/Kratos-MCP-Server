# Tutorial: NACA0012 airfoil

Incompressible, laminar flow around a NACA0012 airfoil (unit chord) at a
small ~4° angle of attack — freestream density 1, dynamic viscosity 0.001
(chord Reynolds number ≈ 1000) — solved end to end through MCP tool calls.
Every call and every number below comes from a real run against Kratos 10.4.

You can hand the whole thing to an assistant as one request:

> Set up the NACA airfoil example, run it, and report the lift and drag
> coefficients.

## Where this mesh comes from

Kratos's own examples repository has three NACA0012 validation cases
(`fluid_dynamics/validation/compressible_naca_0012_Ma_0.8*`), all **transonic
(Ma=0.8), compressible**, using a `CompressibleExplicit` solver with a
potential-flow bootstrap stage and boundary conditions on conservative
variables (momentum/density/total energy). None of that is buildable with
this server today — `add_boundary_condition` only covers `VELOCITY`/
`PRESSURE`-style variables, there's no compressible or potential-flow
template, and `run_simulation` has no multi-stage concept.

This tutorial reuses the real airfoil **geometry** from that reference case
(`naca_0012_geom.mdpa` — a genuine, externally-authored/GiD-meshed,
~21k-node unstructured triangular mesh, not something this server's
structured mesh generator could produce) but drives it as a much simpler
**incompressible laminar flow** through the existing `fluid_transient`
(`Monolithic`/VMS) template. Only the shape is "the classic NACA" — the
physics is deliberately simplified to what the current toolset actually
supports.

Want something to inspect without running any tools at all? The
`kratos://examples/naca-airfoil` MCP resource has the full
ProjectParameters.json/Materials.json and a pre-verified result baked in —
ask an assistant to *"read the kratos naca airfoil example resource"*. The
mesh itself is too large (3.6 MB) to usefully embed in a resource response,
so that one gives a `mdpa_inspect`-style summary instead of the raw file.

## 1. Check the environment

```json
kratos_check_installation()
→ { "importable": true, "version": "10.4...",
    "compiled_applications": ["FluidDynamicsApplication", "LinearSolversApplication", "..."] }
```

## 2. Get the mesh

There's no `mdpa_create_structured_mesh` path for a curved airfoil boundary
— copy the mesh this example ships with
(`src/kratos_mcp/examples/naca_airfoil/mesh.mdpa`) into your case directory,
then inspect it like any other mesh:

```json
mdpa_inspect({"path": "/tmp/naca/mesh.mdpa"})
→ { "num_nodes": 21191, "num_elements": 41147, "num_conditions": 1233,
    "elements_by_type": {"Element2D3N": 41147},
    "conditions_by_type": {"LineCondition2D2N": 1233},
    "sub_model_parts": {
      "FluidParts_Fluid": {...}, "AutomaticInlet2D_Left": {...},
      "Outlet2D_Right": {...}, "NoSlip2D_Top": {...},
      "NoSlip2D_Bottom": {...}, "NoSlip2D_Aerofoil": {...} } }
```

A gotcha worth knowing: the raw mesh's element/condition type names
(`Element2D3N`/`LineCondition2D2N`) don't need to match anything the
`fluid_transient` template expects. Kratos's `ReplaceElementsAndConditionsProcess`
replaces every element/condition unconditionally, based purely on node
count and `domain_size` (e.g. 3-node elements + 2D → `VMS2D3N`), not by
matching the original type name — so an externally-authored mesh with
whatever generic names its source tool gave it just works.

## 3. Scaffold the case

`fluid_transient` defaults to a small rectangular channel (`left`/`right`/
`top`/`bottom`, `FluidModelPart.domain`); override every placeholder that
names a submodelpart to match this mesh's actual names, plus the physical
properties:

```json
create_project({
  "directory": "/tmp/naca", "template": "fluid_transient", "name": "naca_airfoil",
  "overrides": {
    "volume_part": "FluidParts_Fluid",
    "skin_parts": ["AutomaticInlet2D_Left", "Outlet2D_Right",
                   "NoSlip2D_Top", "NoSlip2D_Bottom", "NoSlip2D_Aerofoil"],
    "inlet_model_part": "FluidModelPart.AutomaticInlet2D_Left",
    "inlet_velocity": [0.99756, 0.06976, 0.0],
    "outlet_model_part": "FluidModelPart.Outlet2D_Right",
    "material_model_part": "FluidModelPart.FluidParts_Fluid",
    "density": 1.0, "dynamic_viscosity": 0.001,
    "end_time": 2.0, "time_step": 0.05,
    "nodal_results": ["VELOCITY", "PRESSURE", "REACTION"]
  }
})
```

`inlet_velocity` is a unit vector at ~4° (`[cos 4°, sin 4°, 0]`) rather than
`[1, 0, 0]` — the mesh is built at 0° geometric incidence (NACA0012 is
symmetric), so applying the freestream at an angle is the only way to get a
non-trivial lift-generating case without a second mesh.

## 4. Apply the no-slip walls

The template's built-in processes cover the inlet/outlet; three walls still
need fixing to zero velocity — the far-field top/bottom and the airfoil
surface itself:

```json
add_boundary_condition({
  "parameters_file": "/tmp/naca/ProjectParameters.json",
  "kind": "fix_velocity", "model_part": "FluidModelPart.NoSlip2D_Top",
  "value": [0.0, 0.0, 0.0]
})
```

Repeat for `FluidModelPart.NoSlip2D_Bottom` and
`FluidModelPart.NoSlip2D_Aerofoil`.

One more setting worth adding by hand (not exposed by `add_boundary_condition`,
since it's a solver setting, not a process): `compute_reactions: true` in
`solver_settings`. That's what makes `REACTION` available at the no-slip
nodes afterward — sum it over the airfoil surface for a drag/lift estimate.

## 5. Validate and run

```json
validate_case({"case_dir": "/tmp/naca"})
→ { "valid": true, "issues": [] }

run_simulation({"case_dir": "/tmp/naca", "wait_seconds": 300})
→ { "job_id": "...", "state": "succeeded", "elapsed_seconds": 243.0 }
```

40 time steps (`end_time=2.0s`, `time_step=0.05s`) took about 4 minutes —
this mesh (21k nodes) is far bigger than the structural/thermal examples, so
budget more wall-clock time and either poll `job_status` or use a generous
`wait_seconds`.

## 6. Post-process

```json
results_list({"case_dir": "/tmp/naca"})
→ { "results": { "vtk": ["vtk_output/FluidModelPart_0_1.vtk", "...", "vtk_output/FluidModelPart_0_40.vtk"] } }

results_summary({"file": "/tmp/naca/vtk_output/FluidModelPart_0_40.vtk"})
→ { "statistics": { "VELOCITY": { "max_magnitude": 1.29 }, "PRESSURE": { "min": -0.23, "max": 0.56 } } }
```

`results_probe`/`results_render` work the same as any other case; computing
lift and drag needs one extra step this server doesn't wrap in a tool yet —
summing `REACTION` over the airfoil's nodes, done in plain Python against
the VTK file (`meshio`), matching nodes by nearest coordinate:

```python
import meshio, numpy as np
from kratos_mcp import mdpa as mdpa_mod

mesh = meshio.read("/tmp/naca/vtk_output/FluidModelPart_0_40.vtk")
m = mdpa_mod.read("/tmp/naca/mesh.mdpa")
aerofoil_coords = np.array([m.nodes[i] for i in m.sub_model_parts["NoSlip2D_Aerofoil"].nodes])

pts = np.asarray(mesh.points)
idx = [np.linalg.norm(pts - c, axis=1).argmin() for c in aerofoil_coords]
force_on_wall = -np.asarray(mesh.point_data["REACTION"])[idx].sum(axis=0)  # Newton's 3rd law

q = 0.5 * 1.0 * 1.0**2 * 1.0  # 0.5 * density * U^2 * chord
print("Cd =", force_on_wall[0] / q, " Cl =", force_on_wall[1] / q)
```

**Cd ≈ 0.124, Cl ≈ 0.122** at `t=2.0s`. Both had already settled to a
quasi-steady value — Cd decayed monotonically from 0.24 at the first
checked step, Cl rose from 0.11 and plateaued around 0.12–0.13 over the
last several steps.

## 7. Preview the result

```json
results_render({
  "file": "/tmp/naca/vtk_output/FluidModelPart_0_40.vtk",
  "variable": "PRESSURE", "camera": "xy", "show_edges": false,
  "crop_bounds": [-0.5, 2.0, -0.75, 0.75]
})
```

`crop_bounds` is essential here and new in this tutorial: the airfoil (unit
chord) sits inside a domain that extends from x=-12.5 to x=20 — at
full-domain zoom the airfoil is an invisible speck. `crop_bounds` clips to a
region of interest ([xmin, xmax, ymin, ymax]) before the camera frames the
shot, same idea for `results_animate`.

The rendered image shows the expected stagnation point (high pressure) at
the leading edge and a low-pressure region developing over the front of the
upper surface — the classic lift-generating pressure signature, even at
this modest angle of attack and Reynolds number.

## 8. Sanity check against thin-airfoil theory

Thin-airfoil theory: `Cl ≈ 2π·sin(α) ≈ 0.44` for `α=4°`. Our computed
`Cl ≈ 0.122` is about **3.6× lower** — expected, not a bug:

- thin-airfoil theory is *inviscid* potential flow around an *infinitely
  thin* flat plate;
- this is a *viscous* (Re≈1000) run around a real 12%-thick NACA0012
  section — both effects reduce circulation (and therefore lift) relative
  to the idealised theory.

Drag coefficient (`Cd ≈ 0.124`) is in the right order of magnitude for a
laminar external flow at this Reynolds number (dominated by pressure drag
from the finite-thickness section plus skin friction), though there's no
single canonical reference value to check it against the way there is for
the cantilever's beam theory.

## Variations

- **Higher/lower angle of attack**: change the `inlet_velocity` direction
  (keep it a unit vector times the freestream speed) — larger angles should
  increase `Cl` up to the point where this coarse a mesh and this low a
  Reynolds number no longer resolve separation well.
- **Different Reynolds number**: change `dynamic_viscosity` (`Re = density ×
  U × chord / dynamic_viscosity`); much higher Re will need a finer
  boundary-layer mesh and likely a turbulence model this server doesn't
  expose yet to remain accurate.
- **The literal reference case** (Ma=0.8, compressible, potential-flow
  bootstrap, shock capturing) is a stretch goal beyond this server's current
  scope — see Kratos's own examples repository
  (`fluid_dynamics/validation/compressible_naca_0012_Ma_0.8` and its `_aoa_3`/
  `multistage_` variants) if you want to see what that looks like in raw
  Kratos Python.
