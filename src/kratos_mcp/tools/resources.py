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

    @mcp.resource("kratos://jobs/{job_id}/log")
    def job_log_resource(job_id: str) -> str:
        """Live stdout/stderr log of a simulation job."""
        try:
            return jobs.log_path(job_id).read_text(errors="replace")
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
