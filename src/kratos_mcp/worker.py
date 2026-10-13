"""Worker process: the ONLY code that imports KratosMultiphysics.

Invoked by bridge.run_op() as `python -m kratos_mcp.worker --request-file
req.json --result-file out.json` with PYTHONPATH/LD_LIBRARY_PATH already
pointing at a Kratos build. The result is written to --result-file as
{"ok": true, "result": ...} or {"ok": false, "error": "..."}; stdout is
never used for data because Kratos prints a banner on import."""

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

    args: {"parameters_file": path, "solver_module": optional module path,
           "stage_name": optional stage selector}
    """
    import KratosMultiphysics as KM

    issues: list[str] = []
    warnings: list[str] = []
    checked = False
    with open(args["parameters_file"]) as f:
        try:
            params = KM.Parameters(f.read())
        except RuntimeError as exc:
            return {"valid": False, "issues": [f"Kratos could not parse the JSON: {exc}"],
                    "warnings": [], "deep_validated": False}

    if args.get("stage_name") is not None:
        name = args["stage_name"]
        if not params.Has("stages") or not params["stages"].Has(name) or not params["stages"][name].Has("stage_settings"):
            return {"valid": False, "issues": ["stage_settings missing"],
                    "warnings": [], "deep_validated": False}
        params = params["stages"][name]["stage_settings"]

    for key in ("problem_data", "solver_settings"):
        if not params.Has(key):
            issues.append(f"Missing required top-level key '{key}'")

    solver_module = args.get("solver_module")
    if solver_module and params.Has("solver_settings"):
        try:
            cls = _find_solver_class(solver_module)
            defaults = cls.GetDefaultParameters()
        except (ImportError, OSError) as exc:
            warnings.append(f"Solver unavailable ({solver_module}): {exc}")
        except Exception as exc:
            warnings.append(f"Could not load defaults for {solver_module}: {exc}")
        else:
            settings = params["solver_settings"].Clone()
            # Same top-level validation PythonSolver.ValidateSettings performs.
            try:
                settings.ValidateAndAssignDefaults(defaults)
            except RuntimeError as exc:
                issues.append(f"solver_settings validation failed: {exc}")
            checked = True

    return {"valid": not issues, "issues": issues, "warnings": warnings,
            "deep_validated": checked}


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


def op_mpi_support(args: dict[str, Any]) -> dict[str, Any]:
    """Whether this build can run distributed (KratosMultiphysics.mpi, Trilinos)."""
    info: dict[str, Any] = {"mpi_module": False, "trilinos": False}
    try:
        import KratosMultiphysics.mpi  # noqa: F401
        info["mpi_module"] = True
    except Exception as exc:
        info["mpi_error"] = f"{type(exc).__name__}: {exc}"
    try:
        import KratosMultiphysics.TrilinosApplication  # noqa: F401
        info["trilinos"] = True
    except Exception:
        pass
    return info


def op_meshing_support(args: dict[str, Any]) -> dict[str, Any]:
    """Whether this build can remesh: MeshingApplication importable and compiled with MMG
    (``mmg`` = the 2D and 3D processes; ``mmg2d`` / ``mmg3d`` / ``mmg3d_surfaces`` per variant)."""
    info: dict[str, Any] = {"meshing": False, "mmg": False}
    try:
        import KratosMultiphysics.MeshingApplication as MA
    except Exception as exc:
        info["meshing_error"] = f"{type(exc).__name__}: {exc}"
        return info
    info["meshing"] = True
    for key, name in (("mmg2d", "MmgProcess2D"), ("mmg3d", "MmgProcess3D"),
                      ("mmg3d_surfaces", "MmgProcess3DSurfaces")):
        info[key] = hasattr(MA, name)
    info["mmg"] = info["mmg2d"] and info["mmg3d"]
    return info


def _scalar_variable(name: str):
    import KratosMultiphysics as KM

    try:
        variable = KM.KratosGlobals.GetVariable(name)
    except Exception as exc:
        raise ValueError(f"Unknown Kratos variable '{name}'") from exc
    if "Double" not in type(variable).__name__:
        raise ValueError(f"Variable '{name}' is a {type(variable).__name__}; remeshing fields "
                         "must be scalar (Double) variables")
    return variable


def op_remesh_mdpa(args: dict[str, Any]) -> dict[str, Any]:
    """Remesh an .mdpa with MMG (MeshingApplication's mmg_process) and write the result.

    args: {"input": stem-or-path, "output": stem-or-path, "applications": [...],
           "request": remesh.build_request(...)} — ``assign`` lists the nodal
    expressions evaluated on the input mesh before remeshing (metric drivers);
    ``check`` lists expressions whose interpolated values are compared with the
    exact expression on the new nodes."""
    import KratosMultiphysics as KM
    from kratos_mcp import remesh

    for app in [*args.get("applications", DEFAULT_MDPA_APPLICATIONS), "MeshingApplication"]:
        try:
            importlib.import_module(f"KratosMultiphysics.{app}")
        except ImportError:
            if app == "MeshingApplication":
                raise
    from KratosMultiphysics.MeshingApplication import mmg_process

    request = args["request"]
    settings = dict(request["settings"])
    stem_in = args["input"][:-5] if args["input"].endswith(".mdpa") else args["input"]
    stem_out = args["output"][:-5] if args["output"].endswith(".mdpa") else args["output"]

    model = KM.Model()
    root = model.CreateModelPart(settings["model_part_name"])
    variables = {name: _scalar_variable(name)
                 for name in [*request.get("read_variables", []),
                              *(item["variable"] for item in [*request["assign"],
                                                              *request["check"]])]}
    if settings.get("strategy") == "levelset":
        variables.setdefault("DISTANCE", KM.DISTANCE)
        variables["DISTANCE_GRADIENT"] = KM.DISTANCE_GRADIENT
    for variable in variables.values():
        root.AddNodalSolutionStepVariable(variable)
    root.ProcessInfo[KM.DOMAIN_SIZE] = request["domain_size"]
    KM.ModelPartIO(stem_in).ReadModelPart(root)

    def assign(items: list[dict[str, str]]) -> None:
        for item in items:
            var = variables[item["variable"]]
            if item.get("source") == "mesh":
                continue  # already loaded from the input mesh's NodalData block
            if "values" in item:
                for node in root.Nodes:
                    node.SetSolutionStepValue(var, item["values"][str(node.Id)])
                continue
            fn = remesh.compile_expression(item["expression"])
            for node in root.Nodes:
                node.SetSolutionStepValue(var, fn(node.X, node.Y, node.Z))

    assign(request["assign"])
    assign(request["check"])
    field_items = [i for i in request["assign"] if "values" in i or i.get("source") == "mesh"]
    field_ranges_before = {
        i["variable"]: [min(n.GetSolutionStepValue(variables[i["variable"]]) for n in root.Nodes),
                        max(n.GetSolutionStepValue(variables[i["variable"]]) for n in root.Nodes)]
        for i in field_items}
    before = {"nodes": root.NumberOfNodes(), "elements": root.NumberOfElements(),
              "conditions": root.NumberOfConditions()}

    process = mmg_process.Factory(KM.Parameters(json.dumps({"Parameters": settings})), model)
    process.ExecuteInitialize()

    mode = KM.IO.WRITE | KM.IO.MESH_ONLY if hasattr(KM.IO, "MESH_ONLY") else KM.IO.WRITE
    KM.ModelPartIO(stem_out, mode).WriteModelPart(root)

    transfer: dict[str, Any] = {}
    for item in request["check"]:
        fn = remesh.compile_expression(item["expression"])
        var = variables[item["variable"]]
        errors, values = [], []
        for node in root.Nodes:
            exact = fn(node.X, node.Y, node.Z)
            values.append(exact)
            errors.append(abs(node.GetSolutionStepValue(var) - exact))
        transfer[item["variable"]] = {
            "expression": item["expression"], "max_abs_error": max(errors),
            "mean_abs_error": sum(errors) / len(errors),
            "value_range": [min(values), max(values)]}

    for item in field_items:
        nodal = [n.GetSolutionStepValue(variables[item["variable"]]) for n in root.Nodes]
        transfer[item["variable"]] = {
            "source": "field", "value_range_before": field_ranges_before[item["variable"]],
            "value_range_after": [min(nodal), max(nodal)]}

    fields = {name: {str(node.Id): node.GetSolutionStepValue(variables[name])
                     for node in root.Nodes}
              for name in request.get("write_fields", [])}

    return {"written_to": stem_out + ".mdpa", "fields": fields,
            "kratos_counts": {"before": before, "after": {
                "nodes": root.NumberOfNodes(), "elements": root.NumberOfElements(),
                "conditions": root.NumberOfConditions()}},
            "transfer": transfer}


OPS = {
    "check": op_check,
    "mpi_support": op_mpi_support,
    "meshing_support": op_meshing_support,
    "remesh_mdpa": op_remesh_mdpa,
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
