"""Simulation runner: executes one Kratos case to completion.

Runs INSIDE the Kratos environment as `python -m kratos_mcp.runner
--case-dir DIR --parameters ProjectParameters.json`, spawned detached by
jobs.JobManager. All output goes to stdout/stderr (redirected to the job
log by the manager). The exit code is the job result.

Author: Vicente Mataix Ferrándiz
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys


# analysis_type (as used by our templates and tools) -> (module, class)
ANALYSIS_CLASSES: dict[str, tuple[str, str]] = {
    "structural": (
        "KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis",
        "StructuralMechanicsAnalysis",
    ),
    "fluid": (
        "KratosMultiphysics.FluidDynamicsApplication.fluid_dynamics_analysis",
        "FluidDynamicsAnalysis",
    ),
    "thermal": (
        "KratosMultiphysics.ConvectionDiffusionApplication.convection_diffusion_analysis",
        "ConvectionDiffusionAnalysis",
    ),
    "potential_flow": (
        "KratosMultiphysics.CompressiblePotentialFlowApplication.potential_flow_analysis",
        "PotentialFlowAnalysis",
    ),
}

# Heuristics mapping ProjectParameters solver_type prefixes to analysis types,
# used when --analysis-type is not given explicitly.
_SOLVER_TYPE_HINTS = [
    (("static", "dynamic", "eigen_value", "prebuckling", "harmonic"), "structural"),
    (("monolithic", "fractional_step", "embedded", "compressible", "two_fluids"), "fluid"),
    (("transient", "stationary", "conjugate", "thermal"), "thermal"),
    (("potential_flow", "ale_potential_flow"), "potential_flow"),
]


def find_analysis_class_in_module(module_path: str):
    """Locate the AnalysisStage subclass defined in a module.

    ProjectParameters.json may carry an 'analysis_stage' key naming the
    module (the convention used by Kratos tests and our templates).
    """
    import inspect

    module = importlib.import_module(module_path)
    candidates = [
        obj for name, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__ and name.endswith("Analysis")
    ]
    if not candidates:
        raise RuntimeError(f"No *Analysis class found in module '{module_path}'")
    candidates.sort(key=lambda c: len(c.__mro__), reverse=True)
    return candidates[0]


def detect_analysis_type(parameters) -> str:
    solver_type = ""
    if parameters.Has("solver_settings") and parameters["solver_settings"].Has("solver_type"):
        solver_type = parameters["solver_settings"]["solver_type"].GetString().lower()
    for prefixes, analysis in _SOLVER_TYPE_HINTS:
        if any(solver_type.startswith(p) for p in prefixes):
            return analysis
    raise RuntimeError(
        f"Cannot infer analysis type from solver_type '{solver_type}'. "
        "Pass --analysis-type or --analysis-class explicitly."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--parameters", default="ProjectParameters.json")
    parser.add_argument("--analysis-type", default=None, choices=sorted(ANALYSIS_CLASSES))
    parser.add_argument(
        "--analysis-class", default=None,
        help="Fully qualified 'module:Class' overriding the built-in dispatch map",
    )
    ns = parser.parse_args()

    os.chdir(ns.case_dir)  # Kratos resolves mdpa/materials paths relative to cwd

    import KratosMultiphysics as KM

    with open(ns.parameters) as f:
        parameters = KM.Parameters(f.read())

    if ns.analysis_class:
        module_path, _, class_name = ns.analysis_class.partition(":")
        if not class_name:
            raise SystemExit("--analysis-class must be 'module.path:ClassName'")
        analysis_cls = getattr(importlib.import_module(module_path), class_name)
    elif ns.analysis_type is None and parameters.Has("analysis_stage"):
        analysis_cls = find_analysis_class_in_module(parameters["analysis_stage"].GetString())
    else:
        analysis_type = ns.analysis_type or detect_analysis_type(parameters)
        module_path, class_name = ANALYSIS_CLASSES[analysis_type]
        analysis_cls = getattr(importlib.import_module(module_path), class_name)

    model = KM.Model()
    simulation = analysis_cls(model, parameters)
    simulation.Run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
