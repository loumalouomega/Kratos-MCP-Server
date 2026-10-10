"""CoSimulation (coupled-solver) case support — pure Python, no Kratos import.

A CoSimulation case is a ProjectParameters.json whose ``solver_settings`` is a
*coupled solver* (``type: coupled_solvers.*``) holding a ``coupling_sequence``
and a ``solvers`` dict; each sub-solver points at its own parameters file via
``solver_wrapper_settings.input_file``. It is driven by
``CoSimulationAnalysis(cosim_settings)`` (see ``runner.py``), not by a
``(model, parameters)`` AnalysisStage.

This module holds the detection helper used for rejections everywhere else,
the static (pre-launch) validator, and the composer behind
``create_cosim_project``. The static checks mirror what Kratos only reports
mid-run (an unknown data name fails inside ``Initialize``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import mdpa as mdpa_mod

# Coupled solvers shipped with CoSimulationApplication (python_scripts/coupled_solvers).
COUPLED_SOLVERS = (
    "coupled_solvers.gauss_seidel_weak",
    "coupled_solvers.jacobi_weak",
    "coupled_solvers.gauss_seidel_strong",
    "coupled_solvers.jacobi_strong",
    "coupled_solvers.block_strong",
    "coupled_solvers.feti_dynamic_coupled_solver",
)

# Data transfer operators (python_scripts/data_transfer_operators).
DATA_TRANSFER_OPERATORS = (
    "copy", "kratos_mapping", "kratos_beam_mapping", "sum_distributed_to_single",
    "copy_single_to_distributed", "copy_with_empty_ranks", "sum_many_to_many",
    "transfer_one_to_many", "3d_1d_data_transfer",
)

# Variables the Python-only SDoF wrappers accept as interface data
# (SdofSolverWrapper.Check / SdofStaticSolverWrapper.Check).
SDOF_VARIABLES = (
    "SCALAR_ROOT_POINT_DISPLACEMENT", "SCALAR_FORCE", "SCALAR_DISPLACEMENT",
    "SCALAR_REACTION", "SCALAR_VOLUME_ACCELERATION",
)
SDOF_STATIC_VARIABLES = ("SCALAR_FORCE", "SCALAR_DISPLACEMENT", "SCALAR_REACTION")
SDOF_WRAPPER = "solver_wrappers.sdof.sdof_solver_wrapper"
SDOF_STATIC_WRAPPER = "solver_wrappers.sdof.static_sdof_solver_wrapper"

# Keys of the SDoF input file (SDoFSolver default_settings).
SDOF_INPUT_KEYS = {
    "system_parameters": {"mass", "stiffness", "damping", "modulus_self_weight"},
    "time_integration_parameters": {"alpha_m", "start_time", "time_step"},
    "initial_values": {"displacement", "velocity", "acceleration"},
    "boundary_conditions": {
        "load_impulse", "omega_force", "omega_root_point_displacement",
        "excitation_function_force", "excitation_function_root_point_displacement",
        "amplitude_root_point_displacement", "amplitude_force",
    },
    "solver_parameters": {"buffer_size"},
    "output_parameters": {"write_output_file", "file_name"},
}

_STRONG = ("gauss_seidel_strong", "jacobi_strong", "block_strong")

# Vector variables commonly exchanged by Kratos-wrapped solvers: CouplingInterfaceData
# raises '"dimension" has to be specified for vector variables!' for them.
VECTOR_VARIABLES = frozenset({
    "DISPLACEMENT", "VELOCITY", "ACCELERATION", "REACTION", "POINT_LOAD", "LINE_LOAD",
    "SURFACE_LOAD", "VOLUME_ACCELERATION", "MESH_DISPLACEMENT", "MESH_VELOCITY",
    "MESH_ACCELERATION", "FORCE", "MOMENT", "ROTATION", "ANGULAR_VELOCITY",
    "ANGULAR_ACCELERATION", "REACTION_MOMENT", "NORMAL", "TORQUE", "CONVECTION_VELOCITY",
    "BODY_FORCE", "FLUID_STRESS", "PRESSURE_GRADIENT",
})

# Mapper types whose ORIGIN interface must carry geometries (conditions/elements).
GEOMETRY_ORIGIN_MAPPERS = frozenset({"nearest_element", "barycentric"})


def is_cosim(params: Any) -> bool:
    """True for a CoSimulation top-level parameters file."""
    if not isinstance(params, dict):
        return False
    solver = params.get("solver_settings")
    if not isinstance(solver, dict):
        return False
    if str(solver.get("type", "")).startswith("coupled_solvers."):
        return True
    return "coupling_sequence" in solver and "solvers" in solver


def input_file_path(case: Path, solver: dict[str, Any]) -> Path | None:
    """Resolve a sub-solver's ``solver_wrapper_settings.input_file`` (Kratos
    resolves it relative to the working directory, with ``.json`` appended
    when missing)."""
    name = (solver.get("solver_wrapper_settings") or {}).get("input_file")
    if not isinstance(name, str) or not name:
        return None
    return case / (name if name.endswith(".json") else f"{name}.json")


def _data_names(solver: dict[str, Any]) -> set[str]:
    data = solver.get("data")
    return set(data) if isinstance(data, dict) else set()


def _check_sequence(settings: dict[str, Any], issues: list[str]) -> None:
    solvers = settings.get("solvers") or {}
    operators = settings.get("data_transfer_operators") or {}
    coupling_ops = settings.get("coupling_operations") or {}
    sequence = settings.get("coupling_sequence") or []

    seen: list[str] = []
    for entry in sequence:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name not in solvers:
            issues.append(f"coupling_sequence references unknown solver '{name}' "
                          f"(declared: {sorted(solvers)})")
            continue
        if name in seen:
            issues.append(f"solver '{name}' appears more than once in coupling_sequence")
        seen.append(name)
    for name in solvers:
        if name not in seen:
            issues.append(f"solver '{name}' is declared in 'solvers' but missing from "
                          "coupling_sequence (Kratos refuses this)")

    def check_item(owner: str, kind: str, i: int, item: dict[str, Any]) -> None:
        where = f"coupling_sequence[{owner}].{kind}[{i}]"
        own = item.get("data")
        if own not in _data_names(solvers.get(owner, {})):
            issues.append(f"{where}: data '{own}' is not declared in solver '{owner}' data")
        far_key, far_data_key = (("from_solver", "from_solver_data") if kind == "input_data_list"
                                 else ("to_solver", "to_solver_data"))
        far = item.get(far_key)
        if far not in solvers:
            issues.append(f"{where}: {far_key} '{far}' is not a declared solver")
        elif item.get(far_data_key) not in _data_names(solvers[far]):
            issues.append(f"{where}: {far_data_key} '{item.get(far_data_key)}' is not declared "
                          f"in solver '{far}' data")
        operator = item.get("data_transfer_operator")
        if operator not in operators:
            issues.append(f"{where}: data_transfer_operator '{operator}' is not defined in "
                          f"data_transfer_operators (defined: {sorted(operators)})")
        for hook in ("before_data_transfer_operations", "after_data_transfer_operations"):
            for op in item.get(hook, []) or []:
                if op not in coupling_ops:
                    issues.append(f"{where}: {hook} references unknown coupling operation '{op}'")

    for entry in sequence:
        if not isinstance(entry, dict) or entry.get("name") not in solvers:
            continue
        for kind in ("input_data_list", "output_data_list"):
            for i, item in enumerate(entry.get(kind, []) or []):
                check_item(entry["name"], kind, i, item)


def _check_convergence(settings: dict[str, Any], issues: list[str], warnings: list[str]) -> None:
    solvers = settings.get("solvers") or {}
    ctype = str(settings.get("type", "")).split(".")[-1]
    criteria = settings.get("convergence_criteria") or []
    if ctype in _STRONG and not criteria:
        issues.append(f"'{settings.get('type')}' needs at least one convergence_criteria entry")
    if ctype not in _STRONG and (criteria or settings.get("convergence_accelerators")):
        warnings.append("convergence_criteria/convergence_accelerators are ignored by a weak coupled solver")
    for key in ("convergence_accelerators", "convergence_criteria"):
        for i, item in enumerate(settings.get(key) or []):
            where = f"{key}[{i}]"
            solver = item.get("solver")
            if solver not in solvers:
                issues.append(f"{where}: solver '{solver}' is not a declared solver")
            elif item.get("data_name") not in _data_names(solvers[solver]):
                issues.append(f"{where}: data_name '{item.get('data_name')}' is not declared "
                              f"in solver '{solver}' data")


def _split_model_part(name: str) -> tuple[str, list[str]]:
    root, *path = str(name).split(".")
    return root, path


def _find_sub_model_part(mesh: mdpa_mod.Mdpa, path: list[str]) -> mdpa_mod.SubModelPart | None:
    if not path:
        return None
    part = mesh.sub_model_parts.get(path[0])
    for name in path[1:]:
        if part is None:
            return None
        part = part.children.get(name)
    return part


def _load_sub_case(case: Path, solver: dict[str, Any]) -> tuple[str | None, mdpa_mod.Mdpa | None]:
    """(main model part name, parsed mesh) of a Kratos-wrapped sub-solver; either
    is ``None`` when it cannot be determined statically (non-mdpa import,
    missing/unparseable file) -- those cases are reported by the regular
    sub-case validation, not here."""
    path = input_file_path(case, solver)
    if path is None or not path.is_file():
        return None, None
    try:
        sub = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None, None
    settings = sub.get("solver_settings") or {}
    main = settings.get("model_part_name")
    imported = settings.get("model_import_settings") or {}
    filename = imported.get("input_filename")
    if imported.get("input_type", "mdpa") != "mdpa" or not filename:
        return main, None
    mdpa_path = case / (filename if filename.endswith(".mdpa") else f"{filename}.mdpa")
    try:
        return main, mdpa_mod.read(mdpa_path)
    except (OSError, ValueError):
        return main, None


def _check_kratos_data(name: str, solver: dict[str, Any], main: str | None,
                       mesh: mdpa_mod.Mdpa | None, issues: list[str]) -> None:
    """Diagnose interface data of a Kratos-wrapped sub-solver before launch
    (Kratos only fails inside ``Initialize``, with little context)."""
    for data_name, data in (solver.get("data") or {}).items():
        where = f"solver '{name}': data '{data_name}'"
        if data.get("variable_name") in VECTOR_VARIABLES and "dimension" not in data:
            issues.append(f"{where}: vector variable '{data['variable_name']}' needs a \"dimension\" "
                          "(2 or 3)")
        model_part = data.get("model_part_name")
        if not model_part:
            issues.append(f"{where}: model_part_name is missing")
            continue
        root, path = _split_model_part(model_part)
        if main is not None and root != main:
            issues.append(f"{where}: model_part_name '{model_part}' does not start with the "
                          f"sub-solver's model part '{main}'")
            continue
        if mesh is None or not path:
            continue
        part = _find_sub_model_part(mesh, path)
        if part is None:
            issues.append(f"{where}: sub-model-part '{model_part}' does not exist in the mesh "
                          f"(available: {sorted(mesh.sub_model_parts)})")
        elif not part.nodes:
            issues.append(f"{where}: sub-model-part '{model_part}' has no nodes")


def _check_mappings(case: Path, settings: dict[str, Any], meshes: dict[str, mdpa_mod.Mdpa | None],
                    issues: list[str], warnings: list[str]) -> None:
    solvers = settings.get("solvers") or {}
    operators = settings.get("data_transfer_operators") or {}
    for op_name, op in operators.items():
        if op.get("type") != "kratos_mapping":
            continue
        mapper = (op.get("mapper_settings") or {}).get("mapper_type", "UNSPECIFIED")
        if mapper in (None, "", "UNSPECIFIED"):
            issues.append(f"data_transfer_operators.{op_name}: kratos_mapping needs "
                          "mapper_settings.mapper_type (e.g. 'nearest_neighbor', 'nearest_element')")

    def check_transfer(where: str, origin_solver: str, origin_data: str, to_solver: str,
                       to_data: str, operator_name: str) -> None:
        operator = operators.get(operator_name) or {}
        if operator.get("type") != "kratos_mapping":
            return
        for side, solver_name, data_name in (("origin", origin_solver, origin_data),
                                            ("destination", to_solver, to_data)):
            data = ((solvers.get(solver_name) or {}).get("data") or {}).get(data_name) or {}
            if "node" not in str(data.get("location", "node_historical")):
                issues.append(f"{where}: kratos_mapping only supports nodal data, but {side} data "
                              f"'{solver_name}.{data_name}' has location '{data.get('location')}'")
        mapper = (operator.get("mapper_settings") or {}).get("mapper_type")
        mesh = meshes.get(origin_solver)
        if mapper in GEOMETRY_ORIGIN_MAPPERS and mesh is not None:
            data = ((solvers.get(origin_solver) or {}).get("data") or {}).get(origin_data) or {}
            _, path = _split_model_part(data.get("model_part_name", ""))
            part = _find_sub_model_part(mesh, path)
            if part is not None and not (part.conditions or part.elements):
                issues.append(f"{where}: mapper '{mapper}' needs geometries on its origin interface, "
                              f"but '{data.get('model_part_name')}' of solver '{origin_solver}' has no "
                              "conditions or elements (Kratos fails with \"No Elements and Conditions "
                              "are present\"); add e.g. line conditions to that sub-model-part")

    for entry in settings.get("coupling_sequence") or []:
        if not isinstance(entry, dict):
            continue
        owner = entry.get("name")
        for i, item in enumerate(entry.get("input_data_list", []) or []):
            check_transfer(f"coupling_sequence[{owner}].input_data_list[{i}]", item.get("from_solver"),
                           item.get("from_solver_data"), owner, item.get("data"),
                           item.get("data_transfer_operator"))
        for i, item in enumerate(entry.get("output_data_list", []) or []):
            check_transfer(f"coupling_sequence[{owner}].output_data_list[{i}]", owner,
                           item.get("data"), item.get("to_solver"), item.get("to_solver_data"),
                           item.get("data_transfer_operator"))


def _check_sdof(case: Path, name: str, solver: dict[str, Any], issues: list[str],
                warnings: list[str]) -> None:
    allowed = SDOF_STATIC_VARIABLES if solver.get("type") == SDOF_STATIC_WRAPPER else SDOF_VARIABLES
    for data_name, data in (solver.get("data") or {}).items():
        var = data.get("variable_name")
        if var not in allowed:
            issues.append(f"solver '{name}': data '{data_name}' uses variable '{var}', but the SDoF "
                          f"solver only allows {list(allowed)}")
        if data.get("location", "node_historical") != "model_part":
            issues.append(f"solver '{name}': data '{data_name}' must set \"location\": \"model_part\" "
                          "(the SDoF wrapper stores scalars on the model part itself)")
    path = input_file_path(case, solver)
    if path is None:
        issues.append(f"solver '{name}': solver_wrapper_settings.input_file is missing")
        return
    if not path.is_file():
        issues.append(f"solver '{name}': input file {path.name} not found in {case}")
        return
    try:
        sdof = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        issues.append(f"solver '{name}': invalid JSON in {path.name}: {exc}")
        return
    for section, body in sdof.items():
        if section not in SDOF_INPUT_KEYS:
            issues.append(f"solver '{name}': {path.name}: unknown section '{section}' "
                          f"(allowed: {sorted(SDOF_INPUT_KEYS)})")
        elif isinstance(body, dict):
            for key in body:
                if key not in SDOF_INPUT_KEYS[section]:
                    issues.append(f"solver '{name}': {path.name}: unknown key '{section}.{key}'")


def validate(case_dir: str | Path, params: dict[str, Any],
             validate_kratos_solver: Callable[[str, Path, dict[str, Any]], dict[str, Any]] | None = None
             ) -> dict[str, Any]:
    """Static validation of a CoSimulation case.

    ``validate_kratos_solver(solver_name, input_file, solver_settings)`` is
    called for each ``solver_wrappers.kratos.*`` sub-solver whose input file
    exists and must return ``{"issues": [...], "warnings": [...]}``; it is how
    ``scaffold`` plugs in the regular single-stage mesh/material validation
    (and, with ``deep``, the Kratos-side check) without a circular import.
    """
    case = Path(case_dir)
    issues: list[str] = []
    warnings: list[str] = []
    settings = params.get("solver_settings") or {}

    for key in ("start_time", "end_time"):
        if key not in (params.get("problem_data") or {}):
            issues.append(f"problem_data.{key} is missing")
    ctype = settings.get("type")
    if ctype not in COUPLED_SOLVERS:
        warnings.append(f"solver_settings.type '{ctype}' is not a known coupled solver "
                        f"(known: {list(COUPLED_SOLVERS)})")
    if not settings.get("solvers"):
        issues.append("solver_settings.solvers is empty")
    if not settings.get("coupling_sequence"):
        issues.append("solver_settings.coupling_sequence is empty")
    if issues:
        return {"valid": False, "issues": issues, "warnings": warnings, "deep_validated": False}

    _check_sequence(settings, issues)
    _check_convergence(settings, issues, warnings)

    for op_name, op in (settings.get("data_transfer_operators") or {}).items():
        if op.get("type") not in DATA_TRANSFER_OPERATORS:
            warnings.append(f"data_transfer_operators.{op_name}: type '{op.get('type')}' is not a "
                            f"known operator (known: {list(DATA_TRANSFER_OPERATORS)})")

    meshes: dict[str, mdpa_mod.Mdpa | None] = {}
    for name, solver in (settings.get("solvers") or {}).items():
        stype = str(solver.get("type", ""))
        if stype in (SDOF_WRAPPER, SDOF_STATIC_WRAPPER):
            _check_sdof(case, name, solver, issues, warnings)
        elif stype.startswith("solver_wrappers.kratos."):
            path = input_file_path(case, solver)
            if path is None:
                issues.append(f"solver '{name}': solver_wrapper_settings.input_file is missing")
            elif not path.is_file():
                issues.append(f"solver '{name}': input file {path.name} not found in {case}")
            else:
                main, mesh = _load_sub_case(case, solver)
                meshes[name] = mesh
                _check_kratos_data(name, solver, main, mesh, issues)
                if validate_kratos_solver is not None:
                    sub = validate_kratos_solver(name, path, solver)
                    issues.extend(f"solver '{name}': {i}" for i in sub.get("issues", []))
                    warnings.extend(f"solver '{name}': {w}" for w in sub.get("warnings", []))
        else:
            warnings.append(f"solver '{name}': wrapper type '{stype}' has no static checks")

    _check_mappings(case, settings, meshes, issues, warnings)

    return {"valid": not issues, "issues": issues, "warnings": warnings, "deep_validated": False}


def summarize(params: dict[str, Any]) -> dict[str, Any]:
    """Structured summary used by ``explain_project_parameters``."""
    problem = params.get("problem_data") or {}
    settings = params.get("solver_settings") or {}
    solvers = {
        name: {"type": s.get("type"),
               "input_file": (s.get("solver_wrapper_settings") or {}).get("input_file"),
               "data": sorted(_data_names(s))}
        for name, s in (settings.get("solvers") or {}).items()
    }
    transfers = []
    for entry in settings.get("coupling_sequence") or []:
        for item in entry.get("input_data_list", []) or []:
            transfers.append({"from": f"{item.get('from_solver')}.{item.get('from_solver_data')}",
                              "to": f"{entry.get('name')}.{item.get('data')}",
                              "operator": item.get("data_transfer_operator"),
                              "options": item.get("data_transfer_operator_options", [])})
        for item in entry.get("output_data_list", []) or []:
            transfers.append({"from": f"{entry.get('name')}.{item.get('data')}",
                              "to": f"{item.get('to_solver')}.{item.get('to_solver_data')}",
                              "operator": item.get("data_transfer_operator"),
                              "options": item.get("data_transfer_operator_options", [])})
    return {
        "analysis_type": "cosimulation",
        "start_time": problem.get("start_time"),
        "end_time": problem.get("end_time"),
        "coupled_solver": settings.get("type"),
        "num_coupling_iterations": settings.get("num_coupling_iterations"),
        "coupling_order": [e.get("name") for e in settings.get("coupling_sequence") or []],
        "solvers": solvers,
        "transfers": transfers,
        "convergence_criteria": settings.get("convergence_criteria") or [],
        "convergence_accelerators": settings.get("convergence_accelerators") or [],
    }


def compose(solvers: dict[str, dict[str, Any]], couplings: list[dict[str, Any]],
            order: list[str] | None = None, coupled_solver: str = "gauss_seidel_weak",
            start_time: float = 0.0, end_time: float = 1.0,
            num_coupling_iterations: int = 10,
            convergence_criteria: list[dict[str, Any]] | None = None,
            convergence_accelerators: list[dict[str, Any]] | None = None,
            data_transfer_operators: dict[str, dict[str, Any]] | None = None,
            echo_level: int = 1) -> dict[str, Any]:
    """Build the top-level CoSimulation ProjectParameters dict.

    ``solvers``: ``{name: {"type": wrapper, "input_file": "<stem>", "data": {...}}}``.
    ``couplings``: ``[{"from_solver", "from_data", "to_solver", "to_data",
    "operator"="copy", "options"=[]}]`` — each is an *output* of the producing
    solver's turn when the producer precedes the consumer in ``order``, else an
    *input* of the consumer (so data always reaches the consumer before its solve
    in the same iteration when it can, and carries the previous iteration's
    value otherwise — the usual Gauss-Seidel semantics).
    """
    order = list(order) if order else list(solvers)
    operators = {"copy": {"type": "copy"}}
    operators.update(data_transfer_operators or {})
    sequence = {name: {"name": name, "input_data_list": [], "output_data_list": []} for name in order}
    for c in couplings:
        src, dst = c["from_solver"], c["to_solver"]
        if src not in sequence or dst not in sequence:
            raise ValueError(f"coupling references solver not in order: {src} -> {dst}")
        operator = c.get("operator", "copy")
        options = c.get("options", [])
        if order.index(src) < order.index(dst):
            sequence[src]["output_data_list"].append({
                "data": c["from_data"], "to_solver": dst, "to_solver_data": c["to_data"],
                "data_transfer_operator": operator, "data_transfer_operator_options": options})
        else:
            sequence[dst]["input_data_list"].append({
                "data": c["to_data"], "from_solver": src, "from_solver_data": c["from_data"],
                "data_transfer_operator": operator, "data_transfer_operator_options": options})
    settings: dict[str, Any] = {
        "type": coupled_solver if coupled_solver.startswith("coupled_solvers.")
        else f"coupled_solvers.{coupled_solver}",
        "echo_level": echo_level,
    }
    if settings["type"].split(".")[-1] in _STRONG:
        settings["num_coupling_iterations"] = num_coupling_iterations
        settings["convergence_accelerators"] = convergence_accelerators or []
        settings["convergence_criteria"] = convergence_criteria or []
    settings["data_transfer_operators"] = operators
    settings["coupling_sequence"] = [sequence[name] for name in order]
    settings["solvers"] = {
        name: {"type": s["type"],
               "solver_wrapper_settings": {"input_file": s["input_file"]},
               "data": s.get("data", {})}
        for name, s in solvers.items()
    }
    return {
        "problem_data": {"problem_name": "cosimulation", "start_time": start_time,
                         "end_time": end_time, "echo_level": echo_level,
                         "print_colors": False, "parallel_type": "OpenMP"},
        "solver_settings": settings,
    }
