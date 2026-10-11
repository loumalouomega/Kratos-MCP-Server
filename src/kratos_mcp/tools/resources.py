"""MCP resources: templates, format guides, worked examples, job logs."""

from __future__ import annotations

import json
from pathlib import Path

from .. import jobs
from .. import mdpa as mdpa_mod
from .scaffold import TEMPLATES_DIR, load_registry, render_template_file

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

MESH_EDITING_GUIDE = """\
# Building and editing meshes with the mdpa_* / mesh_* tools

All editing tools read a .mdpa, apply ONE operation and write it back (in place by
default, or to `output`; an existing `output` is only replaced with overwrite=true).
They return the operation summary, the mesh summary and validation `issues`.
A file that contains blocks the editor cannot represent (Tables, ElementalData,
ConditionalData, vector NodalData) is refused for in-place edits -- give `output`.

## Selecting nodes
`selector` = intersection of: ids, part, box {min,max}, sphere {center,radius},
plane {point,normal}, expression (<= 0, in x/y/z), tolerance, all.
Check it with mdpa_select_nodes before writing anything.

## Typical recipes
- Named boundary:   mdpa_create_sub_model_part(name, selector=..., include=['condition'])
- Load surface:     mdpa_extract_boundary(condition_name, sub_model_part, selector=plane)
- Plate -> solid:   mdpa_extrude(heights=[...]) ; solid -> tets: mdpa_convert_cells('hex_to_tet')
- Join blocks:      mdpa_transform(translate) ; mdpa_merge(merge_tolerance=1e-9)
- Odd shapes:       mdpa_create_geometry_mesh | mesh_generate_gmsh (optional) | mesh_import
- Inspect:          mdpa_get_entities, mdpa_quality (inverted cells), mdpa_validate

## Conventions worth knowing
- Conditions from mdpa_extract_boundary and the generators are oriented with the domain
  on their LEFT (outward normal on the right). In Kratos a positive
  POSITIVE_FACE_PRESSURE then acts along that outward normal (a tension); a compressive
  pressure needs a NEGATIVE value (verified on a plate and a Lame thick cylinder).
- Mirrors / negative scales re-order connectivity so measures stay positive.
- hex_to_tet is a Kuhn split: conforming on structured meshes with consistent node order.
- extrude puts cap nodes in parts zmin / zmax; give cap_condition_names for cap conditions.
- Geometries (`Begin Geometries`) are read, edited and written; SubModelPartGeometries too.
"""

MDPA_FORMAT_GUIDE = """\
# Kratos MDPA mesh format

An `.mdpa` file is a plain-text block format. Every block is delimited by
`Begin <BlockName> [arguments]` and `End <BlockName>`; `//` starts a comment.

## Core blocks

```
Begin ModelPartData        // global key-value data, usually empty
End ModelPartData

Begin Properties 1         // one block per property/material id
End Properties

Begin Nodes                // <id> <x> <y> <z>
    1  0.0 0.0 0.0
    2  1.0 0.0 0.0
End Nodes

Begin Elements SmallDisplacementElement2D4N   // <id> <property_id> <node ids...>
    1  1  1 2 3 4
End Elements

Begin Conditions LineLoadCondition2D2N        // same layout as elements
    1  1  2 3
End Conditions
```

## SubModelParts

Named subdomains reference existing entities by id and can be nested. They
are how ProjectParameters processes target regions (`Structure.left`):

```
Begin SubModelPart left
    Begin SubModelPartNodes
        1
        4
    End SubModelPartNodes
    Begin SubModelPartElements
    End SubModelPartElements
    Begin SubModelPartConditions
    End SubModelPartConditions
End SubModelPart
```

## Conventions

- Element/condition type names encode dimension and node count, e.g.
  `SmallDisplacementElement3D8N` = 3D, 8 nodes (hexahedron).
- Property id 0 is used for entities without assigned materials.
- The mesh file is referenced from ProjectParameters.json WITHOUT the
  `.mdpa` extension (`"input_filename": "mesh"`).
"""

PROJECT_PARAMETERS_GUIDE = """\
# ProjectParameters.json structure

The single configuration file for a Kratos simulation:

```jsonc
{
  "problem_data": {              // always required
    "problem_name": "case",
    "parallel_type": "OpenMP",   // or "MPI"
    "echo_level": 1,             // 0 = silent
    "start_time": 0.0,
    "end_time": 1.0
  },
  "solver_settings": {           // always required; schema depends on solver
    "solver_type": "Static",     // e.g. Static/Dynamic (structural),
                                 // transient/stationary (thermal),
                                 // Monolithic/FractionalStep (fluid)
    "model_part_name": "Structure",
    "domain_size": 2,
    "model_import_settings":    { "input_type": "mdpa", "input_filename": "mesh" },
    "material_import_settings": { "materials_filename": "Materials.json" },
    "time_stepping": { "time_step": 0.1 },
    "linear_solver_settings": { "solver_type": "LinearSolversApplication.sparse_lu" }
  },
  "processes": {                 // boundary conditions and loads
    "constraints_process_list": [ /* Dirichlet BCs */ ],
    "loads_process_list":       [ /* Neumann loads (structural) */ ]
  },
  "output_processes": {
    "vtk_output": [ /* ParaView output */ ],
    "gid_output": []
  },
  "analysis_stage": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis"
}
```

Process blocks follow one pattern:

```json
{
  "python_module": "assign_vector_variable_process",
  "kratos_module": "KratosMultiphysics",
  "process_name": "AssignVectorVariableProcess",
  "Parameters": {
    "model_part_name": "Structure.left",
    "variable_name": "DISPLACEMENT",
    "interval": [0.0, "End"],
    "constrained": [true, true, true],
    "value": [0.0, 0.0, 0.0]
  }
}
```

Use the solver's `GetDefaultParameters()` (tool: kratos_get_solver_defaults)
to discover every accepted key in solver_settings.
"""

MATERIALS_GUIDE = """\
# Materials.json structure

Assigns constitutive laws and property values to model parts:

```json
{
  "properties": [{
    "model_part_name": "Structure.domain",
    "properties_id": 1,
    "Material": {
      "constitutive_law": { "name": "LinearElasticPlaneStrain2DLaw" },
      "Variables": {
        "YOUNG_MODULUS": 210000000000.0,
        "POISSON_RATIO": 0.3,
        "DENSITY": 7850.0
      },
      "Tables": {}
    }
  }]
}
```

Common constitutive laws (tool: kratos_list_constitutive_laws):
- Structural: `LinearElastic3DLaw`, `LinearElasticPlaneStrain2DLaw`,
  `LinearElasticPlaneStress2DLaw` (needs `THICKNESS`), `TrussConstitutiveLaw`,
  `BeamConstitutiveLaw`.
- Fluid: `Newtonian2DLaw` / `Newtonian3DLaw` (`DENSITY`, `DYNAMIC_VISCOSITY`).
- Thermal (convection-diffusion): no constitutive law — plain Variables
  (`DENSITY`, `CONDUCTIVITY`, `SPECIFIC_HEAT`).

The file is referenced from
`solver_settings.material_import_settings.materials_filename`.
"""


CANTILEVER_INTRO = """\
# Worked example: 2D cantilever plate (structural_static)

1 m x 0.2 m steel plate, fixed on the left edge, 1 MN/m downward line load
on the right edge. The files below are copied verbatim from
src/kratos_mcp/examples/cantilever/ in this package -- real files on disk,
not text baked into this module, and not rendered from templates/ at
request time. A coarse 4x1 mesh was chosen so the whole thing fits on one
screen (the cantilever-beam tutorial in the docs site uses the same setup
at 20x4 for a result closer to slender-beam theory)."""

CANTILEVER_RESULT = """\
## Verified result

Running this exact case (validate_case -> run_simulation ->
results_probe at the tip, point [1.0, 0.0, 0.0]) gives:

    DISPLACEMENT at the tip: [-3.6684e-05, -2.5312e-04, 0.0]  (metres)
    -> 0.253 mm downward, 0.037 mm sideways

This coarse mesh under-predicts deflection relative to the converged
(fine-mesh) beam-theory estimate of ~0.48 mm -- expected, coarse meshes are
stiffer. Refine by regenerating the mesh with more divisions, e.g.
mdpa_create_structured_mesh(kind='rectangle', size=[1.0, 0.2],
divisions=[20, 4]) instead of [4, 1].

## Reproducing this with the tools instead of copy-pasting

```
mdpa_create_structured_mesh(path='case/mesh.mdpa', kind='rectangle',
                             size=[1.0, 0.2], divisions=[4, 1])
create_project(directory='case', template='structural_static', name='cantilever')
add_boundary_condition(parameters_file='case/ProjectParameters.json',
                        kind='line_load', model_part='Structure.right',
                        modulus=1000000.0, direction=[0.0, -1.0, 0.0])
run_simulation(case_dir='case', wait_seconds=60)
```
"""


NACA_AIRFOIL_INTRO = """\
# Worked example: NACA0012 airfoil, incompressible viscous flow (fluid_transient)

A 2D NACA0012 airfoil (unit chord, leading edge near the origin) in a large
semicircular-inlet / rectangular-outlet far-field domain, incompressible
laminar flow (DENSITY=1, DYNAMIC_VISCOSITY=0.001 -> chord Reynolds number
~1000), freestream applied at a small ~4 degree angle of attack via the
inlet velocity vector -- the mesh itself is built at 0 degree geometric
incidence (NACA0012 is symmetric), so an angle of attack in the velocity
direction is the only way to get a non-trivial lift-generating case out of
it without a second mesh.

mesh.mdpa is a real, externally-authored (GiD) unstructured triangular mesh
reused from Kratos' own examples repository
(fluid_dynamics/validation/compressible_naca_0012_Ma_0.8), not rendered
from templates/ at request time -- there is no structured-mesh generator in
this server that can produce a curved airfoil boundary (mdpa_create_structured_mesh
only does line/rectangle/box). This is a DELIBERATE SIMPLIFICATION of that
literal reference case, which is transonic (Ma=0.8), compressible, and uses
a potential-flow bootstrap stage -- boundary conditions on conservative
variables and multi-stage orchestration this server doesn't expose. Only
the airfoil geometry is reused; the physics is an incompressible Monolithic
(VMS) run through the existing fluid_transient template.

The mesh is too large (~21k nodes, 3.6 MB) to usefully embed verbatim in an
MCP resource response, unlike the tiny cantilever mesh -- a mdpa_inspect-style
summary is given instead. It ships on disk in this package
(src/kratos_mcp/examples/naca_airfoil/) and works with mdpa_inspect /
mdpa_validate / create_project / run_simulation exactly like any other mesh."""

NACA_AIRFOIL_RESULT = """\
## Verified result

Running this exact case (40 time steps, end_time=2.0s, time_step=0.05s,
~4 minutes wall-clock) and summing REACTION over the NoSlip2D_Aerofoil
nodes of the final VTK snapshot (Newton's third law: force on the wall =
-REACTION) gives:

    drag coefficient  Cd = F_x / (0.5 * DENSITY * U^2 * chord) ~= 0.124
    lift coefficient  Cl = F_y / (0.5 * DENSITY * U^2 * chord) ~= 0.122

Both had settled to a quasi-steady value by step 40 (Cd started at 0.24 at
step 5 and decayed monotonically; Cl rose from 0.11 and plateaued around
0.12-0.13) -- a converged laminar solution, not a mid-transient snapshot.

Cross-check against thin-airfoil theory, Cl ~= 2*pi*sin(alpha) ~= 0.44 for
alpha=4 degrees: our Cl is about 3.6x lower. Expected, not a bug -- thin-
airfoil theory is inviscid potential flow around an infinitely thin plate;
this is a viscous (Re~1000) run around a real 12%-thick section, both of
which reduce effective circulation relative to the idealised theory.

## Reproducing this with the tools

The mesh can't be regenerated with mdpa_create_structured_mesh (curved,
unstructured boundary) -- copy the shipped mesh.mdpa into the case
directory and point the other tools at it:

```
create_project(directory='case', template='fluid_transient', name='naca_airfoil',
                overrides={
                    'volume_part': 'FluidParts_Fluid',
                    'skin_parts': ['AutomaticInlet2D_Left', 'Outlet2D_Right',
                                   'NoSlip2D_Top', 'NoSlip2D_Bottom', 'NoSlip2D_Aerofoil'],
                    'inlet_model_part': 'FluidModelPart.AutomaticInlet2D_Left',
                    'inlet_velocity': [0.9976, 0.0698, 0.0],   # U=1, alpha=4 deg
                    'outlet_model_part': 'FluidModelPart.Outlet2D_Right',
                    'material_model_part': 'FluidModelPart.FluidParts_Fluid',
                    'density': 1.0, 'dynamic_viscosity': 0.001,
                    'end_time': 2.0, 'time_step': 0.05,
                    'nodal_results': ['VELOCITY', 'PRESSURE', 'REACTION'],
                })
add_boundary_condition(parameters_file='case/ProjectParameters.json',
                        kind='fix_velocity', model_part='FluidModelPart.NoSlip2D_Top',
                        value=[0.0, 0.0, 0.0])
# ...repeat for NoSlip2D_Bottom and NoSlip2D_Aerofoil
run_simulation(case_dir='case', wait_seconds=300)
```
"""


CAVITY_INTRO = """\
# Worked example: lid-driven cavity (incompressible flow, monolithic)

The classic CFD benchmark. A unit square filled with fluid; the top wall
("lid") slides at u = 1 m/s while the other three walls are no-slip. The
shear dragged in by the lid drives a single large recirculating vortex. The
files below are real files on disk (src/kratos_mcp/examples/lid_driven_cavity/),
read verbatim -- not rendered from templates at request time.

The mesh is a coarse 10x10 unit square of triangles (121 nodes) generated by
this server's own mdpa_create_structured_mesh; a single corner node pins the
pressure (otherwise it is only defined up to a constant). Density 100 and
dynamic viscosity 1 give a chord Reynolds number Re = rho*U*L/mu = 100 -- the
mildest Ghia benchmark, one steady primary vortex, no secondary corner
eddies."""

CAVITY_RESULT = """\
## Verified result

Running this exact case (30 pseudo-time steps to a steady state, ~1 s
wall-clock) and reading the final VTK snapshot:

    lid velocity u on the top row .................. 1.00 m/s (imposed)
    vertical centerline u(y) at x = 0.5, minimum ... -0.116 m/s at y = 0.40

The negative interior velocity is the signature of the recirculation: fluid
dragged forward along the lid returns backward through the cavity interior,
so u(y) reverses sign below the lid. On a fine (Ghia) mesh at Re = 100 that
minimum is about -0.21 at y ~= 0.46; this coarse 10x10 mesh under-resolves the
vortex core (expected -- refine with more divisions for a sharper profile).

## Reproducing this with the tools

```
mdpa_create_structured_mesh(path='case/mesh.mdpa', kind='rectangle',
                             size=[1.0, 1.0], divisions=[10, 10],
                             element_name='Element2D3N', triangles=true)
# (then add a single-node 'corner' sub-model-part for the pressure pin)
create_project(directory='case', template='fluid_transient', name='cavity',
               overrides={'inlet_velocity': [1.0, 0.0, 0.0], 'density': 100.0,
                          'dynamic_viscosity': 1.0})
add_boundary_condition(parameters_file='case/ProjectParameters.json',
                       kind='inlet_velocity', model_part='FluidModelPart.top',
                       value=[1.0, 0.0, 0.0])          # the moving lid
# fix_velocity [0,0,0] on left/right/bottom; pin PRESSURE at the corner node
run_simulation(case_dir='case', wait_seconds=60)
```
"""

PLASTICITY_INTRO = """\
# Worked example: single-element von Mises plasticity (nonlinear structural)

The smallest possible demonstration of a nonlinear constitutive law. One
1x1x1 m hexahedral element (8 nodes) is stretched in x under symmetry
constraints (roller supports on the xmin/ymin/zmin faces, a prescribed x
displacement ramped on xmax). The material is small-strain isotropic von
Mises (J2) plasticity from the built-in preset
`small_strain_plasticity_von_mises_3d` (E = 210 GPa, yield stress = 250 MPa,
perfect plasticity) -- written with create_materials(preset=...). The solver
runs analysis_type "non_linear" with a residual convergence criterion.

Real files on disk (src/kratos_mcp/examples/plasticity_cube/); the mesh is the
8-node cube from mdpa_create_structured_mesh(kind='box', divisions=[1,1,1])."""

PLASTICITY_RESULT = """\
## Verified result

The x displacement on the xmax face is ramped 0.001 m per step over 5 steps
(strain 0.001 -> 0.005). Reading the reaction force in x on the fixed xmin
face at each step:

    step 1  strain 0.001  |Fx| = 2.10e8 N   <- elastic: E*strain*A = 210 MPa
    step 2  strain 0.002  |Fx| = 2.50e8 N   <- yielded
    step 3  strain 0.003  |Fx| = 2.50e8 N   <- plateau
    step 4  strain 0.004  |Fx| = 2.50e8 N
    step 5  strain 0.005  |Fx| = 2.50e8 N

Textbook perfect plasticity: while the strain is below the yield strain
(sigma_y/E = 250e6/210e9 = 0.00119) the response is linear elastic and
|Fx| = E*strain*A exactly (210 MPa at strain 0.001). Once past yield the
reaction plateaus at |Fx| = sigma_y * A = 250 MPa * 1 m^2 = 2.5e8 N no
matter how much further the element is stretched -- the material yields
instead of carrying more load. A LinearElastic law would keep climbing
past 2.5e8; the plateau is the nonlinearity.

## Reproducing this with the tools

```
mdpa_create_structured_mesh(path='case/mesh.mdpa', kind='box',
                            size=[1.0, 1.0, 1.0], divisions=[1, 1, 1])
create_materials('case/Materials.json',
                 [{'model_part_name': 'Structure.domain',
                   'preset': 'small_strain_plasticity_von_mises_3d'}])
# hand-authored ProjectParameters with solver_settings.analysis_type='non_linear',
# roller supports on xmin/ymin/zmin, prescribed '0.001*t' displacement on xmax
run_simulation(case_dir='case', wait_seconds=60)
```
"""

MULTISTAGE_INTRO = """\
# Worked example: multi-stage load steps (orchestrated analysis)

A cantilever solved in TWO sequential stages that share one mesh, driven by
Kratos' SequentialOrchestrator -- the shape create_multistage_project
produces. Stage `load_step_1` applies a 1 MN/m downward line load on the
right edge; stage `load_step_2` reuses the same model part
(input_type "use_input_model_part") and applies 2 MN/m. This is the
`orchestrator` + `stages` + `execution_list` ProjectParameters structure, run
by run_simulation exactly like a single-stage case.

Real files on disk (src/kratos_mcp/examples/multistage_load_steps/); the mesh
is a 10x4 rectangle cantilever (55 nodes) from mdpa_create_structured_mesh."""

MULTISTAGE_RESULT = """\
## Verified result

Running the orchestrated case (both stages, ~1 s wall-clock) and reading each
stage's own VTK output:

    stage load_step_1  (1 MN/m):  tip deflection uy = -4.00e-04 m
    stage load_step_2  (2 MN/m):  tip deflection uy = -8.00e-04 m

The tip deflection doubles from stage 1 to stage 2 -- exactly proportional to
the doubled load, because each stage is a linear static solve. The log shows
"Analysis -START-"/"Analysis -END-" twice: the orchestrator ran both stages in
the execution_list order.

## Reproducing this with the tools

```
create_multistage_project(directory='case', name='cantilever_ms', stages=[
    {'name': 'load_step_1', 'template': 'structural_static'},
    {'name': 'load_step_2', 'template': 'structural_static'}])
# inject an increasing line_load into each stage's loads_process_list
# (1 MN/m then 2 MN/m on Structure.right), then create the shared mesh:
mdpa_create_structured_mesh(path='case/mesh.mdpa', kind='rectangle',
                            size=[1.0, 0.2], divisions=[10, 4])
run_simulation(case_dir='case', wait_seconds=60)   # runs both stages
```
"""


LOAD_STEPPING_INTRO = """# Worked example: load-stepping cantilever (interactive session)

A 5 m x 0.5 m plane-strain steel cantilever (105 nodes, 80 `SmallDisplacementElement2D4N`
quads, E = 210 GPa, nu = 0.3) fixed on `Structure.left`, with a tip line load on
`Structure.right` that is RAMPED with time: `modulus = "1.0e6*t"` N/m, pointing
down. `time_step` is 0.125 (exactly representable) so the 8 steps land on
t = 0.125, 0.25, ..., 1.0 with no floating-point drift.

Unlike the other examples this one is meant to be driven STEP BY STEP with the
`session_*` tools instead of run to completion with `run_simulation`: open it
with `session_start`, advance a couple of steps, inspect the displacement field
and the tip node, advance the rest, and close. Because the problem is linear
the stepped result must equal a batch run of the same files.

Real files on disk (src/kratos_mcp/examples/load_stepping_cantilever/); the
mesh is a 20x4 rectangle from mdpa_create_structured_mesh."""

LOAD_STEPPING_RESULT = """\
## Verified result

Stepping the case with session_advance(steps=1) eight times and reading the tip
node (id 105) after each step:

    step  time   uy(tip) [m]    max von Mises [Pa]
       1  0.125  -9.833e-04     6.046e+06
       2  0.250  -1.967e-03     1.209e+07
       4  0.500  -3.933e-03     2.418e+07
       8  1.000  -7.867e-03     4.837e+07

Both the tip deflection and the peak stress grow linearly with the load
(uy(t) = -7.867e-03 * t, to ~1e-5 relative), and the final state equals a batch
run_simulation of the same case. The deflection is ~9% below the Euler-Bernoulli
estimate for E/(1-nu^2) because this coarse 20x4 mesh is stiffer than the beam.

## Driving it with the tools

```
session_start(case_dir='case')                     # -> session_id, time 0
session_advance(session_id, steps=2)               # t = 0.25
session_field_summary(session_id, 'DISPLACEMENT')  # max |u| and where
session_inspect_node(session_id, 105)              # the loaded tip
session_advance(session_id, steps=6)               # t = 1.0, finished
session_close(session_id)                          # Finalize() + cleanup
```
"""


POTENTIAL_FLOW_INTRO = """# Worked example: NACA0012 perturbation potential flow

This is the small NACA0012 perturbation-compressible benchmark shipped with
Kratos' CompressiblePotentialFlowApplication. The fixture is copied from the
Kratos source tree at [revision
`e740da832999e4da58dd9457f35143274edb4992`](https://github.com/KratosMultiphysics/Kratos/tree/e740da832999e4da58dd9457f35143274edb4992/applications/CompressiblePotentialFlowApplication/tests) and
can be run through `run_simulation` when CompressiblePotentialFlowApplication
and LinearSolversApplication are compiled."""

POTENTIAL_FLOW_RESULT = """## Verified result

The upstream benchmark gives lift coefficient `0.4968313580730855` and
potential jump at node 13 `0.48769319614651147` at time 1.0. The bundled
reference file checks the nodal velocity potential to `1e-6`; the numerical
acceptance test checks the two scalar values independently."""


COUPLED_SDOF_INTRO = """# Worked example: coupled solvers (CoSimulation, two SDoF oscillators)

Two single-degree-of-freedom oscillators coupled as a mass-spring chain by
Kratos' CoSimulationApplication -- the shape create_cosim_project produces.
`sdof_1` (m = 2 kg, k = 800 N/m) hangs from a fixed wall; `sdof_2` (m = 1 kg,
k = 400 N/m, initial acceleration 10 m/s^2 from `load_impulse`) hangs from
`sdof_1`. Each step the two solvers exchange interface data:

- `sdof_1.disp` -> `sdof_2.root_disp` (the lower spring's root point moves with
  the upper mass), exchanged right after `sdof_1` solves;
- `sdof_2.reaction` -> `sdof_1.force` (the lower spring's reaction loads the
  upper mass), exchanged right before `sdof_1` solves.

The coupling is `coupled_solvers.gauss_seidel_strong`: it iterates within each
step, with an Aitken accelerator on `sdof_1.force`, until the force residual
drops below `rel_tolerance` 1e-8 (or `abs_tolerance` 1e-10).

No mesh is needed: the SDoF wrappers are Python-only, which makes this the
cheapest way to see a coupling run. Real Kratos solvers use the same top-level
structure with `solver_wrappers.kratos.*` types (needs
KratosCoSimulationApplication; run_simulation drives it through
CoSimulationAnalysis).

Real files on disk (src/kratos_mcp/examples/coupled_sdof/): the CoSimulation
ProjectParameters.json plus one parameters file per solver (sdof_1.json,
sdof_2.json)."""

COUPLED_SDOF_RESULT = """\
## Verified result

Running the case (50 steps of 0.01 s to t = 0.5 s, well under a second of
wall-clock) and reading the job log and the two solvers' result files
(`sdof_1_results.dat`, `sdof_2_results.dat`, 51 rows each):

    coupling iterations: 125 in total, max 4 in a step, all 50 steps converged
    t = 0.5 s:  u(sdof_1) = -6.165e-04 m,  u(sdof_2) = 2.285e-03 m
    max |u|:    sdof_1 1.779e-03 m,        sdof_2 2.605e-03 m

Interface data exchange check: in every step `sdof_2`'s root-point
displacement equals `sdof_1`'s displacement from the PREVIOUS step, exactly
(difference 0.0). That one-step lag is the SDoF wrapper's behaviour, not an
error: it reports the last accepted displacement, so this example demonstrates
the data flow rather than monolithic accuracy -- a partitioned run is not
expected to match a monolithic two-DoF solve. The log shows the iteration
pattern 1, 4, 1, 4, ... and a harmless Aitken warning when the relaxation
factor reaches its lower bound.

## Reproducing this with the tools

```
create_cosim_project(directory='case', coupled_solver='gauss_seidel_strong',
    solvers=[{'name': 'sdof_1', 'type': 'sdof', 'parameters': {...},
              'data': {'disp': {'variable_name': 'SCALAR_DISPLACEMENT'},
                       'force': {'variable_name': 'SCALAR_FORCE'}}},
             {'name': 'sdof_2', 'type': 'sdof', 'parameters': {...},
              'data': {'root_disp': {'variable_name': 'SCALAR_ROOT_POINT_DISPLACEMENT'},
                       'reaction': {'variable_name': 'SCALAR_REACTION'}}}],
    couplings=[{'from_solver': 'sdof_1', 'from_data': 'disp',
                'to_solver': 'sdof_2', 'to_data': 'root_disp'},
               {'from_solver': 'sdof_2', 'from_data': 'reaction',
                'to_solver': 'sdof_1', 'to_data': 'force'}],
    end_time=0.5, num_coupling_iterations=30,
    convergence_criteria=[{'type': 'relative_norm_initial_residual',
        'solver': 'sdof_1', 'data_name': 'force',
        'abs_tolerance': 1e-10, 'rel_tolerance': 1e-8}],
    convergence_accelerators=[{'type': 'aitken', 'solver': 'sdof_1',
                               'data_name': 'force'}])
validate_case(case_dir='case')      # catches unknown solvers/data/operators
run_simulation(case_dir='case', wait_seconds=60)
results_convergence(job_id)         # coupling iterations per step
```
"""


COUPLED_BLOCKS_INTRO = """# Worked example: coupled solvers (CoSimulation, two meshes, non-matching interface)

Two elastic blocks, each a real Kratos StructuralMechanicsApplication solver
(`solver_wrappers.kratos.structural_mechanics_wrapper`) on its own mesh, split at
x = 1 m and coupled by Dirichlet-Neumann iteration -- the mesh-based counterpart of
the SDoF example. Plane strain, E = 210 GPa, nu = 0.3, each block 1 m x 1 m.

- `block_a` (BlockA, x in [0, 1], 4 x 4 quads) is clamped at x = 0 and is the
  Neumann side: it receives `POINT_LOAD` on its `right` edge.
- `block_b` (BlockB, x in [1, 2], 4 x 6 quads) carries a tension line load
  `1.0e6*t` N/m on its `right` edge and is the Dirichlet side: its `left` edge
  displacement is imposed from `block_a`, and its `REACTION` there is returned.

The interfaces do not match: `BlockA.right` has 5 nodes, `BlockB.left` has 7.
The `kratos_mapping` operator (`nearest_element`) maps `block_a.disp` onto
`block_b`'s nodes, and the same mapper's transpose (`use_transpose` +
`swap_sign`) distributes `block_b`'s nodal reactions onto `block_a`'s nodes, so
the transmitted force is conserved.

Coupling is `coupled_solvers.gauss_seidel_strong` with an Aitken accelerator and a
`relative_norm_initial_residual` criterion, both on `block_a.disp`.

Things this example gets right that are easy to get wrong:

- the interface of the load-receiving block needs `PointLoadCondition2D1N`
  conditions (the nodal `POINT_LOAD` only reaches the right-hand side through
  them); the mapper additionally needs line conditions on both interfaces;
- vector data needs `"dimension": 2`;
- `BlockB.left` is constrained in the Dirichlet process; the coupling then
  overwrites the (fixed) values every iteration;
- the convergence criterion and accelerator watch the data the Neumann side
  PRODUCES. On the imposed side the first iteration's residual is 0/0, which the
  criterion reports as convergence -- a silent false success;
- validate_case cross-checks every `model_part_name` against its solver's mesh
  before launch.

Real files on disk (src/kratos_mcp/examples/coupled_blocks/). Needs
CoSimulationApplication, MappingApplication, StructuralMechanicsApplication and
LinearSolversApplication."""

COUPLED_BLOCKS_RESULT = """\
## Verified result

Running the case (2 steps of 0.5 s, about a second of wall-clock) with Kratos
10.4.4:

    coupling iterations: 13 per step, both steps converged
    force transmitted through the interface:
        t = 0.5 s:  5.000e+05 N  (block_a POINT_LOAD sum 499999.6, block_b REACTION sum -499999.5)
        t = 1.0 s:  1.000e+06 N  (999998.2 / -999998.1)  -- the applied traction x 1 m
    interface x-displacement at t = 1 s, block_a (5 nodes):
        4.291e-06, 4.101e-06, 4.268e-06, 4.101e-06, 4.291e-06 m
    block_b (7 nodes): 4.291e-06 at both ends, 4.157e-06 .. 4.268e-06 inside
    response at t = 1 s is 2.000 x the response at t = 0.5 s (linear elasticity)

The ends of the interface coincide in both blocks and agree to 7 digits; between
them the two discretisations differ by a few percent (4.10e-06 vs 4.16e-06),
which is the mapping error of the non-matching interface, not a coupling error.

Both blocks write VTK files (`vtk_output_block_a/`, `vtk_output_block_b/`) and the
runner records them in `result-index.json` as two series
(`BlockA:vtk_output_block_a:BlockA_0`, `BlockB:vtk_output_block_b:BlockB_0`), so
`results_time_history(source=<index>, variable='DISPLACEMENT', series=...)` works
on either block.

## Reproducing this with the tools

```
validate_case(case_dir='case')            # model parts checked against both meshes
run_simulation(case_dir='case', wait_seconds=60)
results_convergence(job_id)               # 13 coupling iterations per step
results_time_history(source='case/result-index.json', variable='DISPLACEMENT',
                     series='BlockB:vtk_output_block_b:BlockB_0', point=[2.0, 0.0, 0.0])
```
"""


def _example_bundle(template: str, mesh_hint: str) -> str:
    registry = load_registry()
    values = dict(registry[template]["placeholders"])
    pp = render_template_file(template, "ProjectParameters.json", values)
    mats = render_template_file(template, "Materials.json", values)
    return (
        f"# Worked example: {template}\n\n"
        f"{registry[template]['description']}\n\n"
        f"## Mesh\n\n{mesh_hint}\n\n"
        f"## ProjectParameters.json\n\n```json\n{pp}\n```\n\n"
        f"## Materials.json\n\n```json\n{mats}\n```\n"
    )


FSI_FLEXIBLE_FLAP_INTRO = """# Worked example: partitioned FSI (flexible flap in a channel, Mok benchmark)

A 2D channel flow past a flexible flap: the Mok FSI benchmark from Kratos' FSIApplication tests, solved
with Kratos' FSIApplication (`solver_type: "partitioned"`, Dirichlet-Neumann
coupling). The viscous fluid (rho = 956, mu = 0.145) enters through a parabolic
inlet profile ramped over 10 s, bends the elastic flap (E = 2.3e6, nu = 0.45,
plane stress, linear elastic) and the flap's motion deforms the fluid
mesh (ALE). Three sub-solvers live in one `solver_settings`:

- `fluid_solver_settings`: monolithic Navier-Stokes, VMS elements, on `FluidModelPart`
- `structure_solver_settings`: dynamic (Bossak) total-Lagrangian structure on `Structure`
- `mesh_solver_settings`: `mesh_solver_structural_similarity`, moves the fluid mesh

`coupling_settings` holds the interface pair and the non-linear iteration:
`FluidModelPart.FluidNoSlipInterface2D_Interface` <-> `Structure.Interface2D_Structure_interface`
(single-faced, `mapper_face: "unique"`), MVQN_recursive accelerator, `nl_tol` 1e-6.

The meshes are the externally authored (GiD) meshes of Kratos' own FSI validation
test (4556 fluid nodes, 505 structure nodes), reused verbatim; the parameters are
that test's ProjectParameters adapted to Kratos 10.x and shortened to 3 s (30
steps). Adaptations that matter:

- interfaces (and the fluid `volume_model_part_name` / `skin_parts`) are FULL model
  part names (`FluidModelPart.X`); a bare sub model part name makes Kratos 10.x
  fail in `PrepareModelPart` with "not found as root-ModelPart";
- the placeholder `python_process` entries of the upstream file no longer exist
  and are dropped; GiD output is replaced by VTK output (every 5 steps);
- the upstream control-node history is shipped as `reference_control_nodes.json`
  (its `TIME` array has 249 entries against 250 values, so Kratos'
  `FromJsonCheckResultProcess` cannot consume it; compare against VTK output).

Unlike the other examples, the meshes are too large to embed: summaries are given
below, and the full files are on disk (src/kratos_mcp/examples/fsi_flexible_flap/).
Needs FSIApplication, FluidDynamicsApplication, StructuralMechanicsApplication,
MeshMovingApplication and MappingApplication."""

FSI_FLEXIBLE_FLAP_RESULT = """\
## Verified result

Running the case with Kratos 10.4.4 (about 50 s wall-clock): all 30 steps
converge, 3-6 FSI iterations each (`PartitionedFSIBaseSolver: FSI non-linear
iteration convergence achieved in N iterations`). The mesh displacement
(MESH_DISPLACEMENT_X) at the two control nodes of `Control_Nodes` agrees with the
upstream reference history (`reference_control_nodes.json`, one value per 0.1 s):

    step 10 (t = 1 s)  node 2122: 5.390e-04 (ref 5.404e-04)   node 3100: 3.042e-04 (ref 3.054e-04)
    step 20 (t = 2 s)  node 2122: 4.889e-03 (ref 4.898e-03)   node 3100: 1.931e-03 (ref 1.935e-03)
    step 30 (t = 3 s)  node 2122: 1.518e-02 (ref 1.520e-02)   node 3100: 5.651e-03 (ref 5.658e-03)
    pressure at node 2122, step 30: 13.22 (ref 13.22)

The flap tip x-displacement on the structure side (0.015179) equals the fluid
mesh displacement at the same node: the interface transfer is consistent. The tip
keeps moving after 3 s (the full upstream run is 25 s).

## Reproducing this with the tools

```
validate_case(case_dir='case')            # interfaces, meshes, materials cross-checked
run_simulation(case_dir='case', wait_seconds=120)
results_convergence(job_id)               # coupling iterations per step
results_render(source='case/vtk_output/fluid/Parts_Fluid_0_30.vtk', scalar='PRESSURE')
```

For a small case you generate yourself (non-matching interface meshes, fast), use
`create_fsi_project`.
"""

FSI_FLEXIBLE_WALL_NOTE = """\
## Related: generated FSI cases

`create_fsi_project(directory)` writes a smaller, faster FSI case in the same
format: a channel whose floor is an elastic block, with DIFFERENT fluid (31 nodes)
and wall (21 nodes) interface discretizations. With the defaults (Stokes-like flow,
E = 200) the 2 s run takes a few seconds, converges in 3-7 coupling iterations per
step and settles at a floor deflection of about -2.05e-2 (2 % of the channel
height) with an inlet pressure of about 14.7.
"""

ADAPTIVE_REMESHING_INTRO = """# Worked example: adaptive remeshing (MeshingApplication / MMG)

Two uses of MMG, both verified against Kratos 10.4.4 (KratosMeshingApplication
wheel, which bundles MMG):

1. **Offline, `mesh_remesh`** - a unit square (`plate.mdpa`: 10x10 cells split into
   200 triangles, boundary parts `left/right/bottom/top`) is adapted to the sharp
   front of an analytic field, `tanh(30*(x - 0.5))`, with the `hessian` strategy.
   One pass on a coarse mesh under-resolves a feature narrower than the elements,
   so `passes=3` repeats remeshing on the previous result (the field is
   re-evaluated on every new mesh). `request.json` holds the exact arguments.
2. **At setup, `add_remeshing_process`** - `setup_remesh/` is a transient thermal
   case whose mesh carries a `Begin NodalData DISTANCE` block (distance to a disk).
   An `mmg_process` with `strategy: levelset` and `initial_remeshing: true` refines
   the mesh around the disk before the solver builds its strategy. The thermal
   solver lists `DISTANCE`/`DISTANCE_GRADIENT` in `auxiliary_variables_list` so the
   variables exist.

Remeshing inside the solution loop is deliberately not offered: with a standard
solver it crashes (SIGSEGV after the first remesh - the solver's strategy still
points at the old mesh). Studies, checkpoints, MPI launch and sessions refuse cases
with an `mmg_process`.
"""

ADAPTIVE_REMESHING_RESULT = """## Verified result

Offline (`mesh_remesh(**request.json)`, hessian, 3 passes):

| pass | nodes | elements |
|-----:|------:|---------:|
| 1 | 162 | 286 |
| 2 | 269 | 496 |
| 3 | 385 | 724 |

- All four boundary parts keep their conditions and node bounding box
  (`preserved.*.ok` is true); `issues` is empty.
- Maximum P1 interpolation error of `tanh(30*(x - 0.5))` over a 201x201 sampling grid:
  0.436 on `plate.mdpa`, **0.126 on the adapted 724-element mesh**, against 0.237 /
  0.240 on UNIFORM meshes of 657 / 796 elements (`strategy="uniform"`,
  `min_size=0.065` / `0.055`): at the same element count the adapted mesh is about
  twice as accurate. A single pass (286 elements) is no better than the input
  (0.43).
- `check_fields=[{"variable": "TEMPERATURE", "expression": "x + 2*y"}]` reports a
  transfer error of about 1e-15: MMG's interpolation reproduces linear fields to
  round-off.
- Counts vary by a few elements between MMG builds and thread counts; the
  repository tests assert ratios, not exact numbers.

At setup (`run_simulation('setup_remesh')`, 0.3 s in 3 steps, about one second):

- The 231-node / 400-element input mesh (21x11 grid on 1.0 x 0.5, size 0.05) becomes
  **1608 nodes / 3078 triangles** (`minimal_size` 0.02 inside the disk of radius 0.1
  around (0.5, 0.25), growing to `maximal_size` 0.1); the VTK output has 1608 points.
- The Dirichlet condition on `left` is kept (TEMPERATURE reaches 100.0 there).

Use `mesh_remesh` when you want an inspectable adapted .mdpa to feed any later
workflow; use `add_remeshing_process` only when the case must carry its own
setup-time adaptation.
"""

def register(mcp) -> None:

    @mcp.resource("kratos://templates/{name}")
    def template_resource(name: str) -> str:
        """Raw template files (ProjectParameters + Materials) for a template name."""
        registry = load_registry()
        if name not in registry:
            return json.dumps({"error": f"Unknown template '{name}'",
                               "available": sorted(registry)})
        parts = [f"// Template: {name} — {registry[name]['description']}"]
        for f in sorted((TEMPLATES_DIR / name).glob("*.tpl")):
            parts.append(f"// ---- {f.name} ----\n{f.read_text()}")
        return "\n\n".join(parts)

    @mcp.resource("kratos://docs/mdpa-format")
    def mdpa_format_doc() -> str:
        """Guide to the Kratos MDPA mesh file format."""
        return MDPA_FORMAT_GUIDE

    @mcp.resource("kratos://docs/mesh-editing")
    def mesh_editing_doc() -> str:
        """Guide to building and editing meshes with the mdpa_* / mesh_* tools."""
        return MESH_EDITING_GUIDE

    @mcp.resource("kratos://docs/project-parameters")
    def project_parameters_doc() -> str:
        """Guide to the ProjectParameters.json configuration format."""
        return PROJECT_PARAMETERS_GUIDE

    @mcp.resource("kratos://docs/materials")
    def materials_doc() -> str:
        """Guide to the Materials.json format and common constitutive laws."""
        return MATERIALS_GUIDE

    @mcp.resource("kratos://examples/cantilever")
    def cantilever_example() -> str:
        """Complete structural static example: cantilever plate under edge load.
        Copied verbatim from real files on disk (examples/cantilever/) --
        not rendered from the templates at request time, so it works
        identically even if the templates change later."""
        case_dir = EXAMPLES_DIR / "cantilever"
        mesh = (case_dir / "mesh.mdpa").read_text()
        pp = (case_dir / "ProjectParameters.json").read_text()
        mats = (case_dir / "Materials.json").read_text()
        return (
            f"{CANTILEVER_INTRO}\n\n"
            f"## mesh.mdpa\n\n```\n{mesh}```\n\n"
            f"## ProjectParameters.json\n\n```json\n{pp}```\n\n"
            f"## Materials.json\n\n```json\n{mats}```\n\n"
            f"{CANTILEVER_RESULT}"
        )

    @mcp.resource("kratos://examples/thermal-bar")
    def thermal_bar_example() -> str:
        """Complete thermal example: bar with fixed end temperatures."""
        return _example_bundle(
            "thermal_stationary",
            "Generate with mdpa_create_structured_mesh(path='mesh.mdpa', "
            "kind='rectangle', size=[1.0, 0.1], divisions=[20, 2], triangles=true, "
            "element_name='Element2D3N', condition_name='ThermalFace2D2N'); the "
            "convection-diffusion solver replaces the generic elements at import "
            "(use triangles: its mesh checks require simplex meshes). "
            "Fix TEMPERATURE on 'left' and 'right' with add_boundary_condition.")

    @mcp.resource("kratos://examples/naca-airfoil")
    def naca_airfoil_example() -> str:
        """Worked example: NACA0012 airfoil, incompressible viscous flow.
        Unlike cantilever_example(), the ~21k-node mesh is too large to
        embed verbatim -- a mdpa_inspect-style summary is given instead of
        the raw mesh.mdpa text."""
        case_dir = EXAMPLES_DIR / "naca_airfoil"
        mesh_summary = mdpa_mod.read(case_dir / "mesh.mdpa").inspect()
        pp = (case_dir / "ProjectParameters.json").read_text()
        mats = (case_dir / "Materials.json").read_text()
        return (
            f"{NACA_AIRFOIL_INTRO}\n\n"
            f"## mesh.mdpa summary (mdpa_inspect)\n\n```json\n"
            f"{json.dumps(mesh_summary, indent=2)}\n```\n\n"
            f"## ProjectParameters.json\n\n```json\n{pp}```\n\n"
            f"## Materials.json\n\n```json\n{mats}```\n\n"
            f"{NACA_AIRFOIL_RESULT}"
        )

    def _verbatim_example(name: str, intro: str, result: str) -> str:
        """Embed a small on-disk example's three files verbatim (cantilever
        pattern), for meshes small enough to include in full."""
        case_dir = EXAMPLES_DIR / name
        mesh = (case_dir / "mesh.mdpa").read_text()
        pp = (case_dir / "ProjectParameters.json").read_text()
        mats = (case_dir / "Materials.json").read_text()
        return (
            f"{intro}\n\n"
            f"## mesh.mdpa\n\n```\n{mesh}```\n\n"
            f"## ProjectParameters.json\n\n```json\n{pp}```\n\n"
            f"## Materials.json\n\n```json\n{mats}```\n\n"
            f"{result}"
        )

    @mcp.resource("kratos://examples/lid-driven-cavity")
    def lid_driven_cavity_example() -> str:
        """Complete incompressible-flow example: lid-driven cavity (Re=100,
        monolithic Navier-Stokes). Real files on disk, with a verified
        recirculation result. The classic CFD benchmark."""
        return _verbatim_example("lid_driven_cavity", CAVITY_INTRO, CAVITY_RESULT)

    @mcp.resource("kratos://examples/plasticity-cube")
    def plasticity_cube_example() -> str:
        """Complete nonlinear structural example: a single hexahedral element
        under von Mises plasticity (material preset), showing the elastic ->
        plastic transition. Real files on disk with a verified result."""
        return _verbatim_example("plasticity_cube", PLASTICITY_INTRO, PLASTICITY_RESULT)

    @mcp.resource("kratos://examples/multistage-load-steps")
    def multistage_load_steps_example() -> str:
        """Complete multi-stage (orchestrated) example: a cantilever solved in
        two sequential load steps sharing one mesh via the SequentialOrchestrator.
        Real files on disk with a verified per-stage result."""
        return _verbatim_example("multistage_load_steps", MULTISTAGE_INTRO, MULTISTAGE_RESULT)

    @mcp.resource("kratos://examples/load-stepping-cantilever")
    def load_stepping_cantilever_example() -> str:
        """Complete interactive-session example: a cantilever with a time-ramped
        tip load (modulus '1.0e6*t'), meant to be stepped with the session_*
        tools (session_start/session_advance/session_inspect_node/...). Real
        files on disk with a verified step-by-step result."""
        return _verbatim_example("load_stepping_cantilever", LOAD_STEPPING_INTRO,
                                 LOAD_STEPPING_RESULT)

    @mcp.resource("kratos://examples/channel-flow")
    def channel_flow_example() -> str:
        """Incompressible channel flow via the fractional-step solver (cheaper
        per step than monolithic for large meshes). Rendered from the
        fluid_fractional_step template plus a mesh recipe."""
        return _example_bundle(
            "fluid_fractional_step",
            "Generate with mdpa_create_structured_mesh(path='mesh.mdpa', "
            "kind='rectangle', size=[4.0, 1.0], divisions=[40, 10], triangles=true, "
            "element_name='Element2D3N'); apply an inlet VELOCITY on 'left', pin "
            "PRESSURE on 'right' (outlet), and no-slip VELOCITY [0,0,0] on 'top'/"
            "'bottom' with add_boundary_condition. The fractional-step solver "
            "splits the velocity and pressure solves, so it uses the separate "
            "velocity_/pressure_linear_solver_settings shown below.")

    @mcp.resource("kratos://examples/modal-box")
    def modal_box_example() -> str:
        """Modal (eigenvalue) analysis of a 3D block: natural frequencies and
        mode shapes. Rendered from the structural_modal template plus a mesh
        recipe."""
        return _example_bundle(
            "structural_modal",
            "Generate with mdpa_create_structured_mesh(path='mesh.mdpa', "
            "kind='box', size=[1.0, 1.0, 1.0], divisions=[2, 2, 2], "
            "element_name='SmallDisplacementElement3D8N'); fix one face "
            "(e.g. 'xmin') with add_boundary_condition(kind='fix_displacement'). "
            "The FEAST eigensolver returns num_eigenvalues natural frequencies; "
            "no loads or time stepping are needed for a modal analysis.")

    @mcp.resource("kratos://examples/dynamic-cantilever")
    def dynamic_cantilever_example() -> str:
        """Transient (implicit dynamic) structural analysis: a cantilever under
        a time-varying tip load, integrated with the Newmark scheme. Rendered
        from the structural_dynamic template plus a mesh recipe."""
        return _example_bundle(
            "structural_dynamic",
            "Generate with mdpa_create_structured_mesh(path='mesh.mdpa', "
            "kind='rectangle', size=[1.0, 0.2], divisions=[20, 4]); fix 'left' "
            "and apply a time-varying line_load on 'right' with "
            "add_boundary_condition (e.g. modulus ramped via an interval). The "
            "solver marches in time (time_step below) with Newmark integration, "
            "writing DISPLACEMENT/VELOCITY/ACCELERATION each step for animation "
            "with results_animate.")

    @mcp.resource("kratos://examples/potential-flow")
    def potential_flow_example() -> str:
        """Runnable NACA0012 potential-flow benchmark with reference values."""
        case_dir = EXAMPLES_DIR / "potential_flow"
        mesh = (case_dir / "mesh.mdpa").read_text()
        pp = (case_dir / "ProjectParameters.json").read_text()
        reference = (case_dir / "reference_velocity_potential.json").read_text()
        return (f"{POTENTIAL_FLOW_INTRO}\n\n"
                f"## mesh.mdpa\n\n```\n{mesh}```\n\n"
                f"## ProjectParameters.json\n\n```json\n{pp}```\n\n"
                f"## reference_velocity_potential.json\n\n```json\n{reference}\n```\n\n"
                f"{POTENTIAL_FLOW_RESULT}")

    @mcp.resource("kratos://examples/coupled-sdof")
    def coupled_sdof_example() -> str:
        """Complete CoSimulation (coupled-solver) example: two Python-only
        single-degree-of-freedom oscillators coupled as a mass-spring chain with
        strong Gauss-Seidel iteration and an Aitken accelerator. Real files on
        disk with a verified interface data exchange; no mesh needed."""
        case_dir = EXAMPLES_DIR / "coupled_sdof"
        files = "".join(
            f"## {name}\n\n```json\n{(case_dir / name).read_text()}\n```\n\n"
            for name in ("ProjectParameters.json", "sdof_1.json", "sdof_2.json"))
        return f"{COUPLED_SDOF_INTRO}\n\n{files}{COUPLED_SDOF_RESULT}"

    @mcp.resource("kratos://examples/coupled-blocks")
    def coupled_blocks_example() -> str:
        """Complete mesh-based CoSimulation example: two Kratos structural
        blocks with non-matching interfaces coupled by Dirichlet-Neumann
        iteration through kratos_mapping, with verified force conservation and
        indexed sub-solver output. Real files on disk."""
        case_dir = EXAMPLES_DIR / "coupled_blocks"
        files = []
        for name in ("ProjectParameters.json", "block_a.json", "block_b.json",
                     "block_a_materials.json", "block_b_materials.json"):
            files.append(f"## {name}\n\n```json\n{(case_dir / name).read_text()}\n```\n\n")
        for name in ("block_a.mdpa", "block_b.mdpa"):
            files.append(f"## {name}\n\n```\n{(case_dir / name).read_text()}```\n\n")
        return f"{COUPLED_BLOCKS_INTRO}\n\n{''.join(files)}{COUPLED_BLOCKS_RESULT}"

    @mcp.resource("kratos://examples/fsi-flexible-flap")
    def fsi_flexible_flap_example() -> str:
        """Complete partitioned fluid-structure interaction example: the Mok
        flexible-flap benchmark (FSIApplication, Dirichlet-Neumann coupling,
        ALE mesh motion) with a verified comparison against the upstream
        reference history. Real files on disk; the large meshes are summarized
        instead of embedded."""
        case_dir = EXAMPLES_DIR / "fsi_flexible_flap"
        summaries = "".join(
            f"## {name}.mdpa summary (mdpa_inspect)\n\n```json\n"
            f"{json.dumps(mdpa_mod.read(case_dir / f'{name}.mdpa').inspect(), indent=2)}\n```\n\n"
            for name in ("fluid", "structure"))
        files = "".join(
            f"## {name}\n\n```json\n{(case_dir / name).read_text()}\n```\n\n"
            for name in ("ProjectParameters.json", "MaterialsFluid.json", "MaterialsStructure.json"))
        return (f"{FSI_FLEXIBLE_FLAP_INTRO}\n\n{summaries}{files}"
                f"{FSI_FLEXIBLE_FLAP_RESULT}\n{FSI_FLEXIBLE_WALL_NOTE}")

    @mcp.resource("kratos://examples/adaptive-remeshing")
    def adaptive_remeshing_example() -> str:
        """Worked adaptive-remeshing example (MeshingApplication/MMG): a
        hessian-adapted mesh (mesh_remesh, 3 passes) that is twice as accurate as
        a uniform mesh with the same element count, and a thermal case remeshed
        at setup by an mmg_process (add_remeshing_process). Real files on disk
        with verified counts."""
        case_dir = EXAMPLES_DIR / "adaptive_remeshing"
        setup = case_dir / "setup_remesh"
        plate = json.dumps(mdpa_mod.read(case_dir / "plate.mdpa").inspect(), indent=2)
        setup_mesh = json.dumps(mdpa_mod.read(setup / "mesh.mdpa").inspect(), indent=2)
        return (
            f"{ADAPTIVE_REMESHING_INTRO}\n"
            f"## plate.mdpa summary (mdpa_inspect)\n\n```json\n{plate}\n```\n\n"
            f"## request.json (arguments of mesh_remesh)\n\n```json\n"
            f"{(case_dir / 'request.json').read_text()}```\n\n"
            f"## setup_remesh/mesh.mdpa summary (mdpa_inspect; plus a NodalData DISTANCE "
            f"block)\n\n```json\n{setup_mesh}\n```\n\n"
            f"## setup_remesh/ProjectParameters.json\n\n```json\n"
            f"{(setup / 'ProjectParameters.json').read_text()}\n```\n\n"
            f"## setup_remesh/Materials.json\n\n```json\n"
            f"{(setup / 'Materials.json').read_text()}\n```\n\n"
            f"{ADAPTIVE_REMESHING_RESULT}"
        )

    @mcp.resource("kratos://jobs/{job_id}/log")
    def job_log_resource(job_id: str) -> str:
        """Live stdout/stderr log of a simulation job."""
        try:
            return jobs.log_path(job_id).read_text(errors="replace")
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
