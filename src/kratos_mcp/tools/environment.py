"""Introspection tools: what does this Kratos installation provide?"""

from __future__ import annotations

from typing import Any

import anyio

from .. import bridge, kratos_env, source_catalog

# Curated map of solver_type values per analysis type. Grounded in the
# python_solvers_wrapper_* factories of each application; extended
# dynamically with the *_solver.py modules found in the source tree.
KNOWN_SOLVERS: dict[str, dict[str, str]] = {
    "structural": {
        "Static": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_static_solver",
        "Dynamic": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_implicit_dynamic_solver",
        "explicit_dynamic": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_explicit_dynamic_solver",
        "eigen_value": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_eigensolver",
        "harmonic_analysis": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_harmonic_analysis_solver",
        "prebuckling": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_prebuckling_solver",
        "formfinding": "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_formfinding_solver",
    },
    "thermal": {
        "transient": "KratosMultiphysics.ConvectionDiffusionApplication.convection_diffusion_transient_solver",
        "stationary": "KratosMultiphysics.ConvectionDiffusionApplication.convection_diffusion_stationary_solver",
        "conjugate_heat_transfer": "KratosMultiphysics.ConvectionDiffusionApplication.conjugate_heat_transfer_solver",
    },
    "fluid": {
        "Monolithic": "KratosMultiphysics.FluidDynamicsApplication.navier_stokes_monolithic_solver",
        "FractionalStep": "KratosMultiphysics.FluidDynamicsApplication.navier_stokes_solver_fractionalstep",
        "Embedded": "KratosMultiphysics.FluidDynamicsApplication.navier_stokes_embedded_solver",
        "CompressibleExplicit": "KratosMultiphysics.FluidDynamicsApplication.navier_stokes_compressible_explicit_solver",
        "LowMach": "KratosMultiphysics.FluidDynamicsApplication.navier_stokes_low_mach_solver",
    },
    "potential_flow": {
        "potential_flow": "KratosMultiphysics.CompressiblePotentialFlowApplication.potential_flow_solver",
    },
}

_ANALYSIS_APPS = {
    "structural": "StructuralMechanicsApplication",
    "thermal": "ConvectionDiffusionApplication",
    "fluid": "FluidDynamicsApplication",
    "potential_flow": "CompressiblePotentialFlowApplication",
}


async def _bridge_op(op: str, args: dict[str, Any] | None = None, timeout: float = 120.0) -> Any:
    try:
        return await anyio.to_thread.run_sync(
            lambda: bridge.run_op(op, args, timeout=timeout))
    except bridge.BridgeError as exc:
        return {"error": exc.details()}


def _compiled_apps() -> list[str] | None:
    """Compiled application list, or None when Kratos is unavailable."""
    try:
        return bridge.run_op("list_applications")
    except bridge.BridgeError:
        return None


def register(mcp) -> None:

    @mcp.tool()
    async def kratos_check_installation() -> dict[str, Any]:
        """Check that Kratos Multiphysics is available and report version,
        paths, thread count and the list of compiled applications."""
        env = kratos_env.resolve()
        info: dict[str, Any] = {
            "kratos_root": str(env.root) if env.root else None,
            "pythonpath": str(env.pythonpath) if env.pythonpath else None,
            "ld_library_path": str(env.libs) if env.libs else None,
            "source_tree": str(env.source) if env.source else None,
            "pip_installed": env.pip_installed,
        }
        if not kratos_env.is_available(env):
            info["importable"] = False
            info["hint"] = ("Set KRATOS_ROOT to a Kratos checkout containing a compiled "
                            "build (bin/Release), or KRATOS_PYTHONPATH/KRATOS_LIBS directly.")
            return info
        result = await _bridge_op("check")
        if isinstance(result, dict):
            info.update(result)
        return info

    @mcp.tool()
    async def kratos_list_applications() -> dict[str, Any]:
        """List all Kratos applications found in the source tree, flagging
        which ones are compiled (importable) in the current build."""
        source_apps = source_catalog.list_source_applications()
        compiled = await _bridge_op("list_applications")
        compiled_set = set(compiled) if isinstance(compiled, list) else set()
        return {
            "applications": [
                {"name": app, "compiled": app in compiled_set} for app in source_apps
            ],
            "num_compiled": len(compiled_set),
            "num_source": len(source_apps),
        }

    @mcp.tool()
    def kratos_list_elements(
        application: str | None = None, name_filter: str | None = None
    ) -> list[dict[str, Any]]:
        """List element type names registered in Kratos (parsed from
        KRATOS_REGISTER_ELEMENT macros in the C++ sources). Optionally filter
        by application name (e.g. 'StructuralMechanicsApplication') or by a
        substring of the element name (e.g. 'SmallDisplacement')."""
        return source_catalog.list_entities(
            "elements", application, name_filter, _compiled_apps())

    @mcp.tool()
    def kratos_list_conditions(
        application: str | None = None, name_filter: str | None = None
    ) -> list[dict[str, Any]]:
        """List condition type names registered in Kratos (surface/line/point
        conditions used for loads and boundary terms), with optional filters
        as in kratos_list_elements."""
        return source_catalog.list_entities(
            "conditions", application, name_filter, _compiled_apps())

    @mcp.tool()
    def kratos_list_constitutive_laws(
        application: str | None = None, name_filter: str | None = None
    ) -> list[dict[str, Any]]:
        """List constitutive law names registered in Kratos (material models
        such as LinearElastic3DLaw or Newtonian2DLaw), with optional filters
        as in kratos_list_elements."""
        return source_catalog.list_entities(
            "constitutive_laws", application, name_filter, _compiled_apps())

    @mcp.tool()
    async def kratos_list_variables(
        type_filter: str | None = None, name_filter: str | None = None
    ) -> dict[str, Any]:
        """List Kratos variables (DISPLACEMENT, TEMPERATURE, ...) grouped by
        type (double, array_1d_3, bool, ...), introspected from the live
        build. type_filter selects one group; name_filter is a substring."""
        result = await _bridge_op("list_variables")
        if not isinstance(result, dict) or "error" in result:
            return result
        out: dict[str, list[str]] = {}
        for type_name, names in result.items():
            if type_filter and type_name != type_filter:
                continue
            if name_filter:
                names = [n for n in names if name_filter.lower() in n.lower()]
            if names:
                out[type_name] = names
        return out

    @mcp.tool()
    def kratos_list_solvers(analysis_type: str | None = None) -> dict[str, Any]:
        """List the known solver_type values per analysis type (structural,
        thermal, fluid, potential_flow) together with the Python solver
        module implementing each, plus all *_solver.py modules found in the
        application source tree."""
        out: dict[str, Any] = {}
        for atype, solvers in KNOWN_SOLVERS.items():
            if analysis_type and atype != analysis_type:
                continue
            out[atype] = {
                "solver_types": solvers,
                "all_solver_modules_in_source": source_catalog.list_solver_modules(
                    _ANALYSIS_APPS[atype]),
            }
        return out

    @mcp.tool()
    def kratos_list_processes(
        application: str | None = None, name_filter: str | None = None
    ) -> list[dict[str, str]]:
        """List Python process modules (boundary conditions, loads, output,
        utilities) discoverable in the Kratos source tree. These are the
        values usable as 'python_module' in ProjectParameters process lists."""
        procs = source_catalog.list_python_processes()
        if application:
            procs = [p for p in procs if p["application"].lower() == application.lower()]
        if name_filter:
            procs = [p for p in procs if name_filter.lower() in p["module"].lower()]
        return procs

    @mcp.tool()
    async def kratos_get_solver_defaults(
        analysis_type: str, solver_type: str
    ) -> dict[str, Any]:
        """Return the complete default solver_settings parameters for a solver,
        as reported by its GetDefaultParameters(). analysis_type is one of
        structural/thermal/fluid/potential_flow; solver_type one of the values
        from kratos_list_solvers (e.g. 'Static', 'transient', 'Monolithic')."""
        solvers = KNOWN_SOLVERS.get(analysis_type)
        if solvers is None:
            return {"error": f"Unknown analysis_type '{analysis_type}'. "
                             f"Choose from {sorted(KNOWN_SOLVERS)}"}
        module = solvers.get(solver_type)
        if module is None:
            return {"error": f"Unknown solver_type '{solver_type}' for {analysis_type}. "
                             f"Choose from {sorted(solvers)}"}
        return await _bridge_op("get_solver_defaults", {"module": module})
