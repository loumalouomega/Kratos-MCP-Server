"""MCP resources: templates, format guides, worked examples, job logs."""

from __future__ import annotations

import json
from pathlib import Path

from .. import jobs
from .scaffold import TEMPLATES_DIR, load_registry, render_template_file

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
        """Complete structural static example: cantilever plate under edge load."""
        return _example_bundle(
            "structural_static",
            "Generate with mdpa_create_structured_mesh(path='mesh.mdpa', "
            "kind='rectangle', size=[1.0, 0.2], divisions=[20, 4]). Fix the 'left' "
            "edge; apply a line_load on 'right' with add_boundary_condition.")

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

    @mcp.resource("kratos://jobs/{job_id}/log")
    def job_log_resource(job_id: str) -> str:
        """Live stdout/stderr log of a simulation job."""
        try:
            return jobs.log_path(job_id).read_text(errors="replace")
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
