"""MCP prompts: guided workflows for common Kratos tasks."""

from __future__ import annotations


def register(mcp) -> None:

    @mcp.prompt()
    def setup_structural_analysis(
        description: str = "a cantilever plate fixed on the left with a downward load on the right",
    ) -> str:
        """Guided workflow to set up and run a structural analysis."""
        return f"""Set up and run a Kratos structural analysis for: {description}

Follow this workflow with the kratos MCP tools:
1. kratos_check_installation — confirm Kratos is available and
   StructuralMechanicsApplication is compiled.
2. list_templates — pick structural_static (or structural_dynamic/modal).
3. mdpa_create_structured_mesh — create the mesh in a new case directory
   (kind='rectangle' or 'box'); note the boundary submodelparts it creates
   (left/right/top/bottom or xmin/xmax/...).
4. create_project — scaffold ProjectParameters.json and Materials.json into
   the same directory, setting fix_model_part to the support edge and
   material properties in overrides.
5. add_boundary_condition — add the loads (line_load/point_load/self_weight).
6. validate_case — fix any reported issues before running.
7. run_simulation with wait_seconds=60 for small cases; otherwise poll
   job_status and inspect job_logs.
8. results_list + results_summary + results_probe — report displacement
   magnitudes and check the results are physically plausible.
"""

    @mcp.prompt()
    def setup_thermal_analysis(
        description: str = "a plate with a hot left edge and a cold right edge",
    ) -> str:
        """Guided workflow to set up and run a heat-conduction analysis."""
        return f"""Set up and run a Kratos thermal (convection-diffusion) analysis for: {description}

Follow this workflow with the kratos MCP tools:
1. kratos_check_installation — confirm ConvectionDiffusionApplication is compiled.
2. mdpa_create_structured_mesh — create the mesh with triangles=true,
   element_name='Element2D3N' (the solver substitutes the actual thermal
   element at import; its mesh checks need simplex meshes) and
   condition_name='ThermalFace2D2N'.
3. create_project with template thermal_stationary (steady state) or
   thermal_transient, setting conductivity/density/specific_heat and
   fixed_temperature in overrides.
4. add_boundary_condition — fix_temperature on each constrained edge,
   surface_heat_flux/volume_heat_source for heat inputs.
5. validate_case, then run_simulation.
6. results_summary on the final vtk file; results_probe at points of
   interest; report the temperature field range.
"""

    @mcp.prompt()
    def debug_failed_simulation(job_id: str) -> str:
        """Diagnose why a simulation job failed."""
        return f"""Diagnose the failed Kratos job '{job_id}':

1. job_status('{job_id}') — check returncode and progress (how far did it get?).
2. job_logs('{job_id}', tail=200) — read the traceback/error; common causes:
   - "Unknown variable/element/condition": a required application is not
     imported or not compiled (kratos_check_installation).
   - Mesh errors: run mdpa_validate with deep=true on the case mesh.
   - Missing model parts: validate_case cross-checks process model_part_name
     values against the mesh submodelparts.
   - Non-convergence: results_convergence('{job_id}') shows iteration counts;
     reduce time_step, increase max_iteration, or relax tolerances.
3. validate_case on the case directory to catch configuration issues.
4. Explain the root cause and apply the fix, then re-run and confirm.
"""

    @mcp.prompt()
    def postprocess_results(case_dir: str) -> str:
        """Summarise the results of a finished simulation."""
        return f"""Post-process the Kratos results in '{case_dir}':

1. results_list('{case_dir}') — find the output files (vtk time series).
2. results_summary on the last timestep file — list variables and ranges.
3. results_probe at the locations of interest (max-displacement point,
   outlet, hot spot...).
4. results_convergence if the run was nonlinear — comment on robustness.
5. Report: what was computed, headline numbers with units, plausibility
   checks (symmetry, boundary values, expected orders of magnitude).
"""
