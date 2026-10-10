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
    def setup_fluid_analysis(
        description: str = "flow past a body with an inlet velocity, an outlet, and no-slip walls",
    ) -> str:
        """Guided workflow to set up and run an incompressible flow analysis."""
        return f"""Set up and run a Kratos incompressible flow analysis for: {description}

Follow this workflow with the kratos MCP tools:
1. kratos_check_installation — confirm FluidDynamicsApplication is compiled.
2. list_templates — fluid_transient is the only fluid template today
   (transient incompressible Navier-Stokes, Monolithic/VMS solver).
3. Get a mesh: mdpa_create_structured_mesh (kind='rectangle' or 'box') for a
   simple channel, or point at an externally-authored mesh.mdpa for a
   curved/unstructured boundary (e.g. an airfoil) — mdpa_inspect/mdpa_validate
   work on either since the parser is shape-agnostic; only the structured
   generator is limited to line/rectangle/box.
4. create_project with template fluid_transient, setting volume_part,
   skin_parts, inlet_model_part/inlet_velocity and outlet_model_part in
   overrides to match the mesh's actual submodelpart names.
5. add_boundary_condition — kind='fix_velocity' with value=[0,0,0] on every
   no-slip wall submodelpart not already covered by the template's built-in
   inlet/outlet processes (walls, an immersed body's surface, ...).
6. validate_case — fix any reported issues before running.
7. run_simulation with wait_seconds=60 for small cases; otherwise poll
   job_status and inspect job_logs. Element/condition types in the raw mesh
   don't need to match the solver's expected names — Kratos replaces them
   unconditionally by node count and domain_size at import.
8. results_list + results_summary + results_probe — report velocity/pressure
   ranges; if compute_reactions was enabled, sum REACTION over a wall's nodes
   for a body-force (drag/lift) estimate.
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

    @mcp.prompt()
    def interactive_session(
        case_dir: str,
        goal: str = "step the case forward, checking the response after every few steps",
    ) -> str:
        """Guided workflow to drive a case step by step in a live session."""
        return f"""Drive the Kratos case in '{case_dir}' interactively: {goal}

Use a session (a live Model + AnalysisStage kept in memory) instead of
run_simulation when you want to look at the state between steps:
1. validate_case('{case_dir}') — fix problems first; sessions reject
   multi-stage and MPI cases.
2. session_start(case_dir='{case_dir}', isolate=true) — isolate keeps the
   original files untouched. Note the session_id, end_time and model parts.
3. session_get_parameters(session_id, path='/solver_settings') — confirm what
   is actually solved (time step, solver, processes).
4. Loop: session_advance(steps=N) -> session_field_summary(variable=...) ->
   session_inspect_node / session_inspect_element at the hot spot. Check each
   chunk is converged and the response is physically plausible before going on.
5. Optionally change the live state with session_set_nodal_values (pin a DOF,
   perturb a field) and advance again; session_exec_python is available only
   when the server runs with KRATOS_MCP_ALLOW_EXEC=1.
6. session_close(session_id) — runs Finalize() so VTK/GiD output is flushed.
7. Report the history (time, converged) and headline numbers with units.
"""
