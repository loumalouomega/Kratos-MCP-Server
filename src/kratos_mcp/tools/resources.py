"""MCP resources: templates, format guides, worked examples, job logs."""

from __future__ import annotations

import json
from pathlib import Path

from .. import jobs
from .. import mdpa as mdpa_mod
from .scaffold import TEMPLATES_DIR, load_registry, render_template_file

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

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


COUPLED_BAR_INTRO = """# Worked example: mesh-based coupling (CoSimulation, two Kratos solvers)

An elastic bar split in two blocks, each solved by its own StructuralMechanics
solver (`solver_wrappers.kratos.structural_mechanics_wrapper`) and coupled at
the shared interface x = 1 m with Dirichlet-Neumann partitioning:

- `structure_a` (x in [0, 1], E = 3e9 Pa, fixed at x = 0, 4x4 quads) is the
  Neumann block: it receives nodal `POINT_LOAD`s on its interface;
- `structure_b` (x in [1, 2], E = 1e9 Pa, 4x3 quads, traction 1e6 N/m at x = 2)
  is the Dirichlet block: its interface displacement is prescribed.

The interfaces do NOT match (5 nodes on A's side, 4 on B's), so both transfers
go through `kratos_mapping` (MappingApplication, `nearest_element`):

- `structure_a.interface_displacement` -> `structure_b.interface_displacement`
  (plain mapping);
- `structure_b.interface_reaction` (`REACTION`) -> `structure_a.interface_load`
  (`POINT_LOAD`) with the options `swap_sign` and `use_transpose`, the
  conservative (force-preserving) form of the mapping.

The coupled solver is `coupled_solvers.gauss_seidel_strong` (B solves first
each iteration) monitoring the load residual on `structure_a.interface_load`.
Poisson's ratio is 0, so the exact solution is uniaxial: sigma = 1e6 Pa,
interface displacement sigma*L/E_A = 3.333e-4 m, tip displacement
sigma*L/E_A + sigma*L/E_B = 1.3333e-3 m.

Things this example gets right and the validator checks for you:

- vector data need `"dimension": 2`;
- `nearest_element` needs *geometries* on its ORIGIN interface, so both
  interface sub-model-parts carry geometry-only `LineCondition2D2N` (they
  contribute nothing); the point loads of A live in the root model part;
- Dirichlet-Neumann converges when the Neumann block is the stiffer one
  (error factor ~ E_B / E_A = 1/3 per iteration); swap the stiffnesses and
  plain Gauss-Seidel diverges unless you add an accelerator.

Real files on disk (src/kratos_mcp/examples/coupled_bar/): the CoSimulation
ProjectParameters.json, one parameters + materials + mdpa file per block."""

COUPLED_BAR_RESULT = """\
## Verified result

Running the case (one static coupled step, a second or two of wall-clock):

    coupling iterations: 17 (rel. tolerance 1e-6), CONVERGENCE WAS ACHIEVED
    interface load:      sum of structure_a POINT_LOAD = 2.000000e5 N
                         = -sum of structure_b REACTION (force conserved)
    interface ux:        3.338e-4 m mean on A, 3.341e-4 m on B
                         (exact 3.333e-4; 3.320e-4..3.359e-4 across the nodes)
    tip ux (x = 2 m):    1.3336e-3 m mean, 1.3333e-3..1.3338e-3 across the nodes
                         (exact 1.3333e-3)

The small spread across the interface is the mapping error between the two
non-matching node distributions; it vanishes under refinement. Both blocks'
VTK output is indexed (series `StructureA:vtk_output_StructureA:StructureA_0` and
`StructureB:...`), so `results_time_history(source=<job result index>,
series=...)` reads either solver's output after the run.

## Checking a case like this before launch

validate_case statically diagnoses the mistakes that otherwise only surface
inside Kratos' Initialize: a data `model_part_name` whose root is not the
sub-solver's model part or whose sub-model-part is absent from its mesh, a
missing `dimension` on a vector variable, a `kratos_mapping` without
`mapper_type`, non-nodal data on a mapped transfer, and a geometry-based mapper
whose origin interface has no conditions/elements.
"""

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

    @mcp.resource("kratos://examples/coupled-bar")
    def coupled_bar_example() -> str:
        """Complete mesh-based CoSimulation example: an elastic bar split into two
        StructuralMechanics blocks with non-matching interface meshes, coupled by
        Dirichlet-Neumann partitioning through kratos_mapping (conservative load
        transfer). Real files on disk with verified numbers."""
        case_dir = EXAMPLES_DIR / "coupled_bar"
        names = ("ProjectParameters.json", "structure_a.json", "structure_b.json",
                 "MaterialsA.json", "MaterialsB.json")
        files = "".join(f"## {name}\n\n```json\n{(case_dir / name).read_text()}\n```\n\n" for name in names)
        meshes = "".join(f"## {name}\n\n```\n{(case_dir / name).read_text()}```\n\n"
                         for name in ("block_a.mdpa", "block_b.mdpa"))
        return f"{COUPLED_BAR_INTRO}\n\n{files}{meshes}{COUPLED_BAR_RESULT}"

    @mcp.resource("kratos://jobs/{job_id}/log")
    def job_log_resource(job_id: str) -> str:
        """Live stdout/stderr log of a simulation job."""
        try:
            return jobs.log_path(job_id).read_text(errors="replace")
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
