"""Project scaffolding tools: ProjectParameters.json, Materials.json, cases.

Templates live in kratos_mcp/templates/<name>/ as JSON with {{placeholder}}
markers; templates/registry.json holds per-template metadata and defaults.
Substitution rules: a quoted "{{key}}" is replaced by the JSON encoding of
the value (so numbers/arrays stay typed); a bare {{key}} inside a longer
string is replaced textually.

Author: Vicente Mataix Ferrándiz
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anyio

from .. import bridge, mdpa as mdpa_mod
from .environment import KNOWN_SOLVERS

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def load_registry() -> dict[str, Any]:
    return json.loads((TEMPLATES_DIR / "registry.json").read_text())


def render(text: str, values: dict[str, Any]) -> str:
    def quoted(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"Missing template placeholder '{key}'")
        return json.dumps(values[key])

    def bare(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"Missing template placeholder '{key}'")
        return str(values[key])

    text = re.sub(r'"\{\{(\w+)\}\}"', quoted, text)
    return re.sub(r"\{\{(\w+)\}\}", bare, text)


def render_template_file(template: str, filename: str, values: dict[str, Any]) -> str:
    tpl_path = TEMPLATES_DIR / template / f"{filename}.tpl"
    if not tpl_path.is_file():
        raise FileNotFoundError(f"Template '{template}' has no {filename}")
    return render(tpl_path.read_text(), values)


def _resolve_values(template: str, overrides: dict[str, Any] | None) -> dict[str, Any]:
    registry = load_registry()
    if template not in registry:
        raise KeyError(f"Unknown template '{template}'. Available: {sorted(registry)}")
    values = dict(registry[template]["placeholders"])
    for key, val in (overrides or {}).items():
        if key not in values:
            raise KeyError(
                f"Template '{template}' has no placeholder '{key}'. "
                f"Available: {sorted(values)}")
        values[key] = val
    return values


# --------------------------------------------------- process block builders

def _vector_process(model_part: str, variable: str, value: list,
                    constrained: bool, interval: list) -> dict[str, Any]:
    return {
        "python_module": "assign_vector_variable_process",
        "kratos_module": "KratosMultiphysics",
        "process_name": "AssignVectorVariableProcess",
        "Parameters": {
            "model_part_name": model_part,
            "variable_name": variable,
            "interval": interval,
            "constrained": [constrained] * 3,
            "value": value,
        },
    }


def _scalar_process(model_part: str, variable: str, value: float,
                    constrained: bool, interval: list) -> dict[str, Any]:
    return {
        "python_module": "assign_scalar_variable_process",
        "kratos_module": "KratosMultiphysics",
        "process_name": "AssignScalarVariableProcess",
        "Parameters": {
            "model_part_name": model_part,
            "variable_name": variable,
            "interval": interval,
            "constrained": constrained,
            "value": value,
        },
    }


def _direction_to_conditions_process(model_part: str, variable: str, modulus: float,
                                     direction: list, interval: list) -> dict[str, Any]:
    return {
        "python_module": "assign_vector_by_direction_to_condition_process",
        "kratos_module": "KratosMultiphysics",
        "check": "DirectorVectorNonZero direction",
        "process_name": "AssignVectorByDirectionToConditionProcess",
        "Parameters": {
            "model_part_name": model_part,
            "variable_name": variable,
            "interval": interval,
            "modulus": modulus,
            "direction": direction,
        },
    }


def _scalar_to_conditions_process(model_part: str, variable: str, value: float,
                                  interval: list) -> dict[str, Any]:
    return {
        "python_module": "assign_scalar_variable_to_conditions_process",
        "kratos_module": "KratosMultiphysics",
        "process_name": "AssignScalarVariableToConditionsProcess",
        "Parameters": {
            "model_part_name": model_part,
            "variable_name": variable,
            "interval": interval,
            "value": value,
        },
    }


def _direction_process(model_part: str, variable: str, modulus: float,
                       direction: list, interval: list) -> dict[str, Any]:
    return {
        "python_module": "assign_vector_by_direction_process",
        "kratos_module": "KratosMultiphysics",
        "check": "DirectorVectorNonZero direction",
        "process_name": "AssignVectorByDirectionProcess",
        "Parameters": {
            "model_part_name": model_part,
            "variable_name": variable,
            "interval": interval,
            "constrained": False,
            "modulus": modulus,
            "direction": direction,
        },
    }


# kind -> (builder id, default variable, default process list)
BC_KINDS: dict[str, tuple[str, str, str]] = {
    "fix_displacement":   ("vector", "DISPLACEMENT", "constraints_process_list"),
    "prescribed_displacement": ("vector", "DISPLACEMENT", "constraints_process_list"),
    "fix_velocity":       ("vector", "VELOCITY", "boundary_conditions_process_list"),
    "inlet_velocity":     ("vector", "VELOCITY", "boundary_conditions_process_list"),
    "outlet_pressure":    ("scalar", "PRESSURE", "boundary_conditions_process_list"),
    "fix_temperature":    ("scalar", "TEMPERATURE", "constraints_process_list"),
    "point_load":         ("direction_to_conditions", "POINT_LOAD", "loads_process_list"),
    "line_load":          ("direction_to_conditions", "LINE_LOAD", "loads_process_list"),
    "surface_load":       ("direction_to_conditions", "SURFACE_LOAD", "loads_process_list"),
    "pressure_load":      ("scalar_to_conditions", "POSITIVE_FACE_PRESSURE", "loads_process_list"),
    "surface_heat_flux":  ("scalar_to_conditions", "FACE_HEAT_FLUX", "fluxes_process_list"),
    "volume_heat_source": ("scalar", "HEAT_FLUX", "fluxes_process_list"),
    "self_weight":        ("direction", "VOLUME_ACCELERATION", "loads_process_list"),
}


def infer_solver_module(parameters: dict[str, Any]) -> str | None:
    """Best-effort mapping from solver_settings.solver_type to the solver module."""
    solver_type = str(parameters.get("solver_settings", {}).get("solver_type", ""))
    for solvers in KNOWN_SOLVERS.values():
        if solver_type in solvers:
            return solvers[solver_type]
    return None


def _submodelpart_paths(m: mdpa_mod.Mdpa) -> set[str]:
    paths: set[str] = set()

    def walk(part: mdpa_mod.SubModelPart, prefix: str) -> None:
        path = f"{prefix}{part.name}"
        paths.add(path)
        for child in part.children.values():
            walk(child, path + ".")

    for part in m.sub_model_parts.values():
        walk(part, "")
    return paths


def _collect_model_part_refs(node: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "model_part_name" and isinstance(val, str):
                refs.add(val)
            else:
                refs |= _collect_model_part_refs(val)
    elif isinstance(node, list):
        for item in node:
            refs |= _collect_model_part_refs(item)
    return refs


def validate_case_files(case_dir: str | Path, parameters_file: str = "ProjectParameters.json",
                        deep: bool = True) -> dict[str, Any]:
    """Static + (optionally) Kratos-backed validation of a case directory.

    Shared by the validate_project_parameters and validate_case tools.
    """
    case = Path(case_dir).expanduser().resolve()
    issues: list[str] = []
    warnings: list[str] = []

    pfile = case / parameters_file
    if not pfile.is_file():
        return {"valid": False, "issues": [f"{pfile} does not exist"], "warnings": []}
    try:
        params = json.loads(pfile.read_text())
    except json.JSONDecodeError as exc:
        return {"valid": False, "issues": [f"Invalid JSON in {pfile.name}: {exc}"], "warnings": []}

    for key in ("problem_data", "solver_settings"):
        if key not in params:
            issues.append(f"Missing required top-level key '{key}'")

    solver = params.get("solver_settings", {})
    mesh = None
    input_filename = solver.get("model_import_settings", {}).get("input_filename")
    if solver.get("model_import_settings", {}).get("input_type", "mdpa") == "mdpa":
        if not input_filename:
            issues.append("solver_settings.model_import_settings.input_filename is missing")
        else:
            mdpa_path = case / f"{input_filename}.mdpa"
            if not mdpa_path.is_file():
                issues.append(f"Mesh file {mdpa_path.name} not found in {case}")
            else:
                try:
                    mesh = mdpa_mod.read(mdpa_path)
                    issues.extend(mesh.validate())
                except (ValueError, OSError) as exc:
                    issues.append(f"Could not parse {mdpa_path.name}: {exc}")

    materials_filename = solver.get("material_import_settings", {}).get("materials_filename")
    materials = None
    if materials_filename:
        mfile = case / materials_filename
        if not mfile.is_file():
            issues.append(f"Materials file {materials_filename} not found in {case}")
        else:
            try:
                materials = json.loads(mfile.read_text())
            except json.JSONDecodeError as exc:
                issues.append(f"Invalid JSON in {materials_filename}: {exc}")

    # Cross-check model part references against the mesh submodelparts.
    if mesh is not None:
        available = _submodelpart_paths(mesh)
        root = solver.get("model_part_name", "")
        refs = _collect_model_part_refs(params.get("processes", {}))
        refs |= _collect_model_part_refs(params.get("output_processes", {}))
        if materials:
            refs |= _collect_model_part_refs(materials)
        for ref in sorted(refs):
            local = ref[len(root) + 1:] if root and ref.startswith(root + ".") else ref
            if local == root or ref == root:
                continue
            if local not in available:
                issues.append(
                    f"model_part_name '{ref}' does not match any SubModelPart in the mesh "
                    f"(available: {sorted(available)})")

    result: dict[str, Any] = {"valid": not issues, "issues": issues, "warnings": warnings}

    if deep and not issues:
        solver_module = infer_solver_module(params)
        if solver_module is None:
            warnings.append("Could not infer solver module from solver_type; "
                            "skipped Kratos-side solver settings validation")
        try:
            deep_result = bridge.run_op("validate_parameters", {
                "parameters_file": str(pfile),
                "solver_module": solver_module,
            })
            if not deep_result.get("valid", True):
                issues.extend(deep_result.get("issues", []))
                result["valid"] = False
        except bridge.BridgeError as exc:
            warnings.append(f"Kratos-side validation unavailable: {exc}")
    return result


# ------------------------------------------------------------------ register

def register(mcp) -> None:

    @mcp.tool()
    def list_templates() -> dict[str, Any]:
        """List the available case templates (structural_static,
        thermal_transient, fluid_transient, ...) with their descriptions,
        required applications and placeholder defaults."""
        return load_registry()

    @mcp.tool()
    def create_project_parameters(
        template: str,
        output_file: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a ProjectParameters.json from a template (see
        list_templates). overrides replaces placeholder defaults, e.g.
        {"end_time": 2.0, "fix_model_part": "Structure.left"}. When
        output_file is given the JSON is written there; the content is
        always returned."""
        values = _resolve_values(template, overrides)
        content = render_template_file(template, "ProjectParameters.json", values)
        parsed = json.loads(content)  # guarantee valid JSON
        out: dict[str, Any] = {"parameters": parsed}
        if output_file:
            path = Path(output_file).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            out["written_to"] = str(path)
        return out

    @mcp.tool()
    def create_materials(
        output_file: str,
        materials: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Write a Kratos Materials.json. Each entry needs 'model_part_name'
        (e.g. 'Structure.domain') and 'variables' (e.g. {"YOUNG_MODULUS":
        2.1e11}); 'constitutive_law' (e.g. 'LinearElasticPlaneStrain2DLaw')
        is optional (thermal problems have none)."""
        props = []
        for i, mat in enumerate(materials, start=1):
            if "model_part_name" not in mat:
                return {"error": f"materials[{i-1}] is missing 'model_part_name'"}
            entry: dict[str, Any] = {
                "model_part_name": mat["model_part_name"],
                "properties_id": mat.get("properties_id", i),
                "Material": {
                    "Variables": mat.get("variables", {}),
                    "Tables": {},
                },
            }
            if mat.get("constitutive_law"):
                entry["Material"]["constitutive_law"] = {"name": mat["constitutive_law"]}
            props.append(entry)
        content = {"properties": props}
        path = Path(output_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=4))
        return {"written_to": str(path), "materials": content}

    @mcp.tool()
    def create_project(
        directory: str,
        template: str,
        name: str = "case",
        overrides: dict[str, Any] | None = None,
        create_demo_mesh: bool = False,
    ) -> dict[str, Any]:
        """Scaffold a complete Kratos case directory from a template:
        ProjectParameters.json + Materials.json (+ optionally a small demo
        rectangle mesh wired to the template defaults, so the case runs out
        of the box). Returns the created file paths and next steps."""
        registry = load_registry()
        if template not in registry:
            return {"error": f"Unknown template '{template}'. Available: {sorted(registry)}"}
        meta = registry[template]
        values = _resolve_values(template, {"problem_name": name, **(overrides or {})})

        case = Path(directory).expanduser().resolve()
        case.mkdir(parents=True, exist_ok=True)

        pp = render_template_file(template, "ProjectParameters.json", values)
        json.loads(pp)
        (case / "ProjectParameters.json").write_text(pp)

        mats = render_template_file(template, "Materials.json", values)
        json.loads(mats)
        (case / values["materials_filename"]).write_text(mats)

        created = [str(case / "ProjectParameters.json"), str(case / values["materials_filename"])]
        next_steps = []
        mdpa_path = case / f"{values['mdpa_basename']}.mdpa"
        if create_demo_mesh:
            mesh = mdpa_mod.create_rectangle_mesh(
                width=1.0, height=0.2, nx=10, ny=2,
                element_name=meta["default_element"],
                condition_name=meta["default_condition"],
                triangles=meta["default_element"].endswith("3N"),
            )
            mesh.write(mdpa_path)
            created.append(str(mdpa_path))
        else:
            next_steps.append(
                f"Create the mesh at {mdpa_path} (e.g. with mdpa_create_structured_mesh, "
                f"element_name='{meta['default_element']}')")
        next_steps.append(f"Validate with validate_case('{case}')")
        next_steps.append(f"Run with run_simulation('{case}')")
        return {"case_dir": str(case), "created": created,
                "required_applications": meta["required_applications"],
                "next_steps": next_steps}

    @mcp.tool()
    def add_boundary_condition(
        parameters_file: str,
        kind: str,
        model_part: str,
        value: list[float] | float | None = None,
        modulus: float | None = None,
        direction: list[float] | None = None,
        interval: list | None = None,
        process_list: str | None = None,
    ) -> dict[str, Any]:
        """Insert a boundary condition / load process block into an existing
        ProjectParameters.json. kind is one of: fix_displacement,
        prescribed_displacement, fix_velocity, inlet_velocity, outlet_pressure,
        fix_temperature, point_load, line_load, surface_load, pressure_load,
        surface_heat_flux, volume_heat_source, self_weight. Loads with
        modulus+direction (point/line/surface_load, self_weight) apply along a
        direction vector; the others take 'value' (vector or scalar)."""
        if kind not in BC_KINDS:
            return {"error": f"Unknown kind '{kind}'. Available: {sorted(BC_KINDS)}"}
        builder, variable, default_list = BC_KINDS[kind]
        interval = interval if interval is not None else [0.0, "End"]

        if builder == "vector":
            vec = value if isinstance(value, list) else [0.0, 0.0, 0.0]
            constrained = kind in ("fix_displacement", "prescribed_displacement",
                                   "fix_velocity", "inlet_velocity")
            block = _vector_process(model_part, variable, vec, constrained, interval)
        elif builder == "scalar":
            scalar = float(value) if value is not None else 0.0
            constrained = kind in ("fix_temperature", "outlet_pressure")
            block = _scalar_process(model_part, variable, scalar, constrained, interval)
        elif builder == "scalar_to_conditions":
            scalar = float(value) if value is not None else 0.0
            block = _scalar_to_conditions_process(model_part, variable, scalar, interval)
        elif builder == "direction_to_conditions":
            if modulus is None or direction is None:
                return {"error": f"kind '{kind}' requires 'modulus' and 'direction'"}
            block = _direction_to_conditions_process(model_part, variable, modulus,
                                                     direction, interval)
        else:  # direction (self_weight)
            block = _direction_process(model_part, variable,
                                       modulus if modulus is not None else 9.81,
                                       direction or [0.0, -1.0, 0.0], interval)

        path = Path(parameters_file).expanduser().resolve()
        params = json.loads(path.read_text())
        processes = params.setdefault("processes", {})
        list_name = process_list or default_list
        processes.setdefault(list_name, []).append(block)
        path.write_text(json.dumps(params, indent=4))
        return {"written_to": str(path), "process_list": list_name, "added": block}

    @mcp.tool()
    def add_output_process(
        parameters_file: str,
        format: str = "vtk",
        variables: list[str] | None = None,
        model_part: str | None = None,
        output_path: str = "vtk_output",
        output_file: str | None = None,
        position: list[float] | None = None,
    ) -> dict[str, Any]:
        """Add an output process to a ProjectParameters.json. format: 'vtk'
        (ParaView files, needs output_path), 'json' (time series of variables
        to a JSON file, needs output_file), or 'point' (probe variables at a
        coordinate, needs position and output_file). variables defaults to
        the ones already used elsewhere in the file or ["DISPLACEMENT"]."""
        path = Path(parameters_file).expanduser().resolve()
        params = json.loads(path.read_text())
        root_mp = params.get("solver_settings", {}).get("model_part_name", "Structure")
        model_part = model_part or root_mp
        variables = variables or ["DISPLACEMENT"]

        if format == "vtk":
            block = {
                "python_module": "vtk_output_process",
                "kratos_module": "KratosMultiphysics",
                "process_name": "VtkOutputProcess",
                "Parameters": {
                    "model_part_name": model_part,
                    "output_control_type": "step",
                    "output_interval": 1,
                    "file_format": "ascii",
                    "output_precision": 7,
                    "output_path": output_path,
                    "save_output_files_in_folder": True,
                    "nodal_solution_step_data_variables": variables,
                },
            }
            target = params.setdefault("output_processes", {}).setdefault("vtk_output", [])
        elif format == "json":
            block = {
                "python_module": "json_output_process",
                "kratos_module": "KratosMultiphysics",
                "process_name": "JsonOutputProcess",
                "Parameters": {
                    "model_part_name": model_part,
                    "output_variables": variables,
                    "output_file_name": output_file or "results.json",
                    "time_frequency": 0.0,
                },
            }
            target = params.setdefault("processes", {}).setdefault("list_other_processes", [])
        elif format == "point":
            if position is None:
                return {"error": "format 'point' requires 'position' [x, y, z]"}
            block = {
                "python_module": "point_output_process",
                "kratos_module": "KratosMultiphysics",
                "process_name": "PointOutputProcess",
                "Parameters": {
                    "model_part_name": model_part,
                    "position": position,
                    "output_variables": variables,
                    "output_file_settings": {
                        "file_name": output_file or "point_output.dat",
                    },
                },
            }
            target = params.setdefault("processes", {}).setdefault("list_other_processes", [])
        else:
            return {"error": f"Unknown format '{format}'. Use vtk, json or point."}

        target.append(block)
        path.write_text(json.dumps(params, indent=4))
        return {"written_to": str(path), "added": block}

    @mcp.tool()
    async def validate_project_parameters(
        parameters_file: str, deep: bool = True
    ) -> dict[str, Any]:
        """Validate a ProjectParameters.json: JSON syntax, required keys,
        referenced files (mesh, materials) exist, model part names match the
        mesh submodelparts, and (deep=true, needs Kratos) the solver_settings
        against the solver's GetDefaultParameters()."""
        pfile = Path(parameters_file).expanduser().resolve()
        return await anyio.to_thread.run_sync(
            lambda: validate_case_files(pfile.parent, pfile.name, deep=deep))
