"""Worker process: the ONLY code that imports KratosMultiphysics.

Invoked by bridge.run_op() as `python -m kratos_mcp.worker --request-file
req.json --result-file out.json` with PYTHONPATH/LD_LIBRARY_PATH already
pointing at a Kratos build. The result is written to --result-file as
{"ok": true, "result": ...} or {"ok": false, "error": "..."}; stdout is
never used for data because Kratos prints a banner on import.

Author: Vicente Mataix Ferrándiz
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
import traceback
from typing import Any


def op_check(args: dict[str, Any]) -> dict[str, Any]:
    import KratosMultiphysics as KM

    info: dict[str, Any] = {"importable": True}
    try:
        info["version"] = KM.KratosGlobals.Kernel.Version()
    except Exception:
        info["version"] = getattr(KM, "__version__", "unknown")
    try:
        info["build_type"] = KM.KratosGlobals.Kernel.BuildType()
    except Exception:
        pass
    try:
        info["python_version"] = sys.version.split()[0]
        info["kratos_module_path"] = KM.__file__
    except Exception:
        pass
    try:
        info["num_threads"] = KM.ParallelUtilities.GetNumThreads()
    except Exception:
        pass
    try:
        info["is_distributed"] = KM.IsDistributedRun()
    except Exception:
        pass
    try:
        from KratosMultiphysics import kratos_utilities
        info["compiled_applications"] = sorted(kratos_utilities.GetListOfAvailableApplications())
    except Exception:
        info["compiled_applications"] = []
    return info


def op_list_applications(args: dict[str, Any]) -> list[str]:
    from KratosMultiphysics import kratos_utilities

    return sorted(kratos_utilities.GetListOfAvailableApplications())


def op_list_variables(args: dict[str, Any]) -> dict[str, list[str]]:
    import KratosMultiphysics as KM

    kernel = KM.KratosGlobals.Kernel
    getters = {
        "double": "GetDoubleVariableNames",
        "array_1d_3": "GetArrayVariableNames",
        "bool": "GetBoolVariableNames",
        "int": "GetIntVariableNames",
        "unsigned_int": "GetUnsignedIntVariableNames",
        "vector": "GetVectorVariableNames",
        "matrix": "GetMatrixVariableNames",
        "string": "GetStringVariableNames",
        "flags": "GetFlagsVariableNames",
    }
    out: dict[str, list[str]] = {}
    for type_name, getter in getters.items():
        if hasattr(kernel, getter):
            try:
                names = getattr(kernel, getter)()
                # The Kernel getters return one newline-separated string.
                if isinstance(names, str):
                    names = names.split()
                out[type_name] = sorted(names)
            except Exception:
                pass
    return out


def op_has_constitutive_laws(args: dict[str, Any]) -> dict[str, bool]:
    """Check which of the given constitutive law names exist in this build.

    args: {"names": [...], "applications": ["StructuralMechanicsApplication", ...]}
    Applications must be imported first: registration happens at import time.
    """
    import KratosMultiphysics as KM

    for app in args.get("applications", []):
        try:
            importlib.import_module(f"KratosMultiphysics.{app}")
        except ImportError:
            pass
    kernel = KM.KratosGlobals.Kernel
    return {name: bool(kernel.HasConstitutiveLaw(name)) for name in args.get("names", [])}


def _find_solver_class(module_path: str):
    module = importlib.import_module(module_path)
    candidates = [
        obj for _, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__ and hasattr(obj, "GetDefaultParameters")
    ]
    if not candidates:
        raise RuntimeError(f"No solver class with GetDefaultParameters found in {module_path}")
    # Prefer the most derived class defined in the module.
    candidates.sort(key=lambda c: len(c.__mro__), reverse=True)
    return candidates[0]


def op_get_solver_defaults(args: dict[str, Any]) -> dict[str, Any]:
    """args: {"module": "KratosMultiphysics.<App>.<solver_module>"}"""
    cls = _find_solver_class(args["module"])
    defaults = cls.GetDefaultParameters()
    return json.loads(defaults.PrettyPrintJsonString())


def op_validate_parameters(args: dict[str, Any]) -> dict[str, Any]:
    """Parse a ProjectParameters.json with Kratos and validate solver settings.

    args: {"parameters_file": path, "solver_module": optional module path}
    """
    import KratosMultiphysics as KM

    issues: list[str] = []
    with open(args["parameters_file"]) as f:
        try:
            params = KM.Parameters(f.read())
        except RuntimeError as exc:
            return {"valid": False, "issues": [f"Kratos could not parse the JSON: {exc}"]}

    for key in ("problem_data", "solver_settings"):
        if not params.Has(key):
            issues.append(f"Missing required top-level key '{key}'")

    solver_module = args.get("solver_module")
    if solver_module and params.Has("solver_settings"):
        try:
            cls = _find_solver_class(solver_module)
            defaults = cls.GetDefaultParameters()
            settings = params["solver_settings"].Clone()
            # Same top-level validation PythonSolver.ValidateSettings performs.
            settings.ValidateAndAssignDefaults(defaults)
        except RuntimeError as exc:
            issues.append(f"solver_settings validation failed: {exc}")
        except Exception as exc:  # import errors etc.
            issues.append(f"Could not validate against {solver_module}: {exc}")

    return {"valid": not issues, "issues": issues}


DEFAULT_MDPA_APPLICATIONS = [
    "StructuralMechanicsApplication",
    "ConvectionDiffusionApplication",
    "FluidDynamicsApplication",
]


def op_read_mdpa_deep(args: dict[str, Any]) -> dict[str, Any]:
    """Round-trip an mdpa file through Kratos ModelPartIO.

    args: {"path": "/abs/path/to/file.mdpa", "applications": [...]}
    Applications must be imported so their element/condition names are
    registered before the file is read.
    """
    import KratosMultiphysics as KM

    for app in args.get("applications", DEFAULT_MDPA_APPLICATIONS):
        try:
            importlib.import_module(f"KratosMultiphysics.{app}")
        except ImportError:
            pass

    path = args["path"]
    if path.endswith(".mdpa"):
        path = path[:-5]
    model = KM.Model()
    mp = model.CreateModelPart("Main")
    KM.ModelPartIO(path).ReadModelPart(mp)

    def describe(part) -> dict[str, Any]:
        return {
            "nodes": part.NumberOfNodes(),
            "elements": part.NumberOfElements(),
            "conditions": part.NumberOfConditions(),
            "sub_model_parts": {
                smp.Name: describe(smp) for smp in part.SubModelParts
            },
        }

    return {"read_ok": True, "model_part": describe(mp)}


OPS = {
    "check": op_check,
    "list_applications": op_list_applications,
    "list_variables": op_list_variables,
    "has_constitutive_laws": op_has_constitutive_laws,
    "get_solver_defaults": op_get_solver_defaults,
    "validate_parameters": op_validate_parameters,
    "read_mdpa_deep": op_read_mdpa_deep,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--result-file", required=True)
    ns = parser.parse_args()

    with open(ns.request_file) as f:
        request = json.load(f)

    payload: dict[str, Any]
    try:
        op = request["op"]
        if op not in OPS:
            raise KeyError(f"Unknown op '{op}'. Available: {sorted(OPS)}")
        payload = {"ok": True, "result": OPS[op](request.get("args", {}))}
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc()}

    with open(ns.result_file, "w") as f:
        json.dump(payload, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
