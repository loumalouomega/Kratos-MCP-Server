"""Partitioned fluid-structure interaction (FSIApplication) support — pure Python,
no Kratos import.

An FSI case is a ProjectParameters.json whose ``solver_settings`` is a
*partitioned* solver (``solver_type: partitioned``) nesting a complete
``fluid_solver_settings``, ``structure_solver_settings`` and
``mesh_solver_settings`` plus a ``coupling_settings`` block (interface pair,
convergence accelerator, non-linear tolerance). Unlike CoSimulation it keeps the
``(model, parameters)`` AnalysisStage constructor: ``FsiAnalysis`` in
``KratosMultiphysics.FSIApplication.fsi_analysis`` (see ``runner.py``).

This module holds the detection helper used for rejections elsewhere, the static
(pre-launch) validator, the explain summary, and the generator behind
``create_fsi_project``. The static checks mirror what Kratos only reports
mid-run (e.g. an interface given as a bare sub model part name fails in
``PrepareModelPart`` with "not found as root-ModelPart").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import mdpa as mdpa_mod

FSI_SOLVER_TYPES = ("partitioned", "partitioned_embedded")
COUPLING_SCHEMES = ("dirichlet_neumann", "dirichletneumann")
# FSIApplication/convergence_accelerator_factory.py
ACCELERATORS = (
    "constant_relaxation", "Relaxation", "MVQN", "MVQN_randomized_SVD", "MVQN_recursive",
    "IBQN_MVQN", "IBQN_MVQN_randomized_SVD",
)
REQUIRED_APPLICATIONS = (
    "FSIApplication", "FluidDynamicsApplication", "StructuralMechanicsApplication",
    "MeshMovingApplication", "MappingApplication",
)
# Process-list keys of FsiAnalysis (_GetOrderOfProcessesInitialization) by domain.
FLUID_PROCESS_LISTS = (
    "fluid_gravity", "fluid_initial_conditions_process_list",
    "fluid_boundary_conditions_process_list", "fluid_auxiliar_process_list",
)
STRUCTURE_PROCESS_LISTS = (
    "structure_constraints_process_list", "structure_contact_process_list",
    "structure_loads_process_list",
)


def is_fsi(params: Any) -> bool:
    """True for a partitioned FSI top-level parameters file."""
    if not isinstance(params, dict):
        return False
    solver = params.get("solver_settings")
    if not isinstance(solver, dict):
        return False
    return str(solver.get("solver_type", "")).lower() in FSI_SOLVER_TYPES


def _mesh_path(case: Path, settings: dict[str, Any]) -> Path | None:
    name = (settings.get("model_import_settings") or {}).get("input_filename")
    if not isinstance(name, str) or not name:
        return None
    return case / (name if name.endswith(".mdpa") else f"{name}.mdpa")


def _local(ref: str, root: str) -> str:
    """'Root.A.B' -> 'A.B'; a bare 'A.B' stays as is (Kratos accepts partial names
    in some solver lists)."""
    return ref[len(root) + 1:] if root and ref.startswith(root + ".") else ref


def _collect_refs(node: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "model_part_name" and isinstance(val, str):
                refs.add(val)
            else:
                refs |= _collect_refs(val)
    elif isinstance(node, list):
        for item in node:
            refs |= _collect_refs(item)
    return refs


def _interface_nodes(mesh: mdpa_mod.Mdpa, path: str) -> list[int] | None:
    part: Any = None
    parts = mesh.sub_model_parts
    for segment in path.split("."):
        part = parts.get(segment)
        if part is None:
            return None
        parts = part.children
    nodes = set(part.nodes)
    for cid in part.conditions:
        for conds in mesh.conditions.values():
            if cid in conds:
                nodes.update(conds[cid][1])
    return sorted(nodes)


def validate(case: Path, params: dict[str, Any],
             submodelpart_paths: Callable[[mdpa_mod.Mdpa], set[str]]) -> dict[str, Any]:
    """Static validation of a partitioned FSI case (no Kratos)."""
    issues: list[str] = []
    warnings: list[str] = []
    solver = params["solver_settings"]
    embedded = str(solver.get("solver_type", "")).lower() == "partitioned_embedded"

    for key in ("problem_data",):
        if key not in params:
            issues.append(f"Missing required top-level key '{key}'")
    scheme = str(solver.get("coupling_scheme", "dirichlet_neumann")).lower()
    if scheme not in COUPLING_SCHEMES:
        issues.append(f"coupling_scheme '{solver.get('coupling_scheme')}' is not available "
                      "(only 'dirichlet_neumann')")

    blocks: dict[str, dict[str, Any]] = {}
    wanted = ["fluid_solver_settings", "structure_solver_settings", "coupling_settings"]
    if not embedded:
        wanted.append("mesh_solver_settings")
    for key in wanted:
        block = solver.get(key)
        if not isinstance(block, dict) or not block:
            issues.append(f"solver_settings.{key} is missing or empty")
        else:
            blocks[key] = block
    fluid = blocks.get("fluid_solver_settings", {})
    structure = blocks.get("structure_solver_settings", {})
    mesh_solver = blocks.get("mesh_solver_settings", {})
    coupling = blocks.get("coupling_settings", {})

    fluid_root = fluid.get("model_part_name", "")
    structure_root = structure.get("model_part_name", "")
    for label, root in (("fluid", fluid_root), ("structure", structure_root)):
        if not root:
            issues.append(f"{label}_solver_settings.model_part_name is missing")
    if fluid_root and fluid_root == structure_root:
        issues.append("fluid and structure use the same model_part_name "
                      f"'{fluid_root}' (they share one Model, so the roots must differ)")
    if mesh_solver and mesh_solver.get("model_part_name") != fluid_root:
        issues.append("mesh_solver_settings.model_part_name must be the fluid model part "
                      f"'{fluid_root}' (the mesh moves with the fluid domain), got "
                      f"'{mesh_solver.get('model_part_name')}'")

    sizes = {label: b.get("domain_size") for label, b in (
        ("fluid", fluid), ("structure", structure), ("mesh", mesh_solver)) if b}
    if len({s for s in sizes.values() if s is not None}) > 1:
        issues.append(f"domain_size differs between the solvers: {sizes}")

    meshes: dict[str, mdpa_mod.Mdpa] = {}
    available: dict[str, set[str]] = {}
    materials_refs: dict[str, set[str]] = {}
    for label, block, root in (("fluid", fluid, fluid_root), ("structure", structure, structure_root)):
        if not block:
            continue
        mdpa_path = _mesh_path(case, block)
        if (block.get("model_import_settings") or {}).get("input_type", "mdpa") != "mdpa":
            warnings.append(f"{label}: non-mdpa input; mesh cross-checks skipped")
        elif mdpa_path is None:
            issues.append(f"{label}_solver_settings.model_import_settings.input_filename is missing")
        elif not mdpa_path.is_file():
            issues.append(f"{label}: mesh file {mdpa_path.name} not found in {case}")
        else:
            try:
                mesh = mdpa_mod.read(mdpa_path)
                issues.extend(f"{label}: {e}" for e in mesh.validate())
                meshes[label] = mesh
                available[label] = submodelpart_paths(mesh)
            except (ValueError, OSError) as exc:
                issues.append(f"{label}: could not parse {mdpa_path.name}: {exc}")
        mats = (block.get("material_import_settings") or {}).get("materials_filename")
        if mats:
            mfile = case / mats
            if not mfile.is_file():
                issues.append(f"{label}: materials file {mats} not found in {case}")
            else:
                try:
                    materials_refs[label] = _collect_refs(json.loads(mfile.read_text()))
                except json.JSONDecodeError as exc:
                    issues.append(f"{label}: invalid JSON in {mats}: {exc}")

    roots = {"fluid": fluid_root, "structure": structure_root}

    def check_part(label: str, ref: str, where: str) -> None:
        if label not in available:
            return
        local = _local(ref, roots[label])
        if local and local != roots[label] and local not in available[label]:
            issues.append(f"{where}: '{ref}' does not match any SubModelPart of the {label} "
                          f"mesh (available: {sorted(available[label])})")

    for label, block in (("fluid", fluid), ("structure", structure)):
        for key in ("volume_model_part_name",):
            if isinstance(block.get(key), str):
                check_part(label, block[key], f"{label}_solver_settings.{key}")
        for key in ("skin_parts", "no_skin_parts", "problem_domain_sub_model_part_list",
                    "processes_sub_model_part_list"):
            for ref in block.get(key) or []:
                if isinstance(ref, str):
                    check_part(label, ref, f"{label}_solver_settings.{key}")
        for ref in sorted(materials_refs.get(label, ())):
            check_part(label, ref, f"{label} materials")

    all_lists = {**{k: "fluid" for k in FLUID_PROCESS_LISTS}, **{k: "structure" for k in STRUCTURE_PROCESS_LISTS}}
    for group_key in ("processes", "output_processes"):
        for list_name, entries in (params.get(group_key) or {}).items():
            for ref in sorted(_collect_refs(entries)):
                root = ref.split(".")[0]
                label = next((l for l, r in roots.items() if r and r == root), None)
                if label is None:
                    if group_key == "processes" and list_name in all_lists:
                        owner = all_lists[list_name]
                        issues.append(f"{group_key}.{list_name}: model_part_name '{ref}' must start with the "
                                      f"{owner} model part '{roots[owner]}'")
                    continue
                check_part(label, ref, f"{group_key}.{list_name}")

    # --- coupling settings -------------------------------------------------
    mappers = coupling.get("mapper_settings")
    interface_pairs: list[tuple[str, str]] = []
    if coupling:
        if not isinstance(mappers, list) or len(mappers) not in (1, 2):
            issues.append("coupling_settings.mapper_settings must list 1 mapper (single-faced "
                          "structure) or 2 (double-faced, 'Positive'/'Negative'), got "
                          f"{len(mappers) if isinstance(mappers, list) else mappers!r}")
            mappers = []
        elif len(mappers) == 2:
            faces = sorted(str(m.get("mapper_face")) for m in mappers)
            if faces != ["Negative", "Positive"]:
                issues.append("with two mappers 'mapper_face' must be 'Positive' and 'Negative'")
        for i, m in enumerate(mappers or []):
            f_name = m.get("fluid_interface_submodelpart_name", "")
            s_name = m.get("structure_interface_submodelpart_name", "")
            if not f_name or not s_name:
                issues.append(f"mapper_settings[{i}] needs fluid_interface_submodelpart_name and "
                              "structure_interface_submodelpart_name")
                continue
            interface_pairs.append((f_name, s_name))
        s_list, f_list = coupling.get("structure_interfaces_list") or [], coupling.get("fluid_interfaces_list") or []
        if not s_list:
            issues.append("coupling_settings.structure_interfaces_list is empty (the first entry is "
                          "the structure interface Kratos couples)")
        for f_name, s_name in interface_pairs:
            if s_list and s_name not in s_list:
                issues.append(f"structure interface '{s_name}' of the mapper is not in structure_interfaces_list")
            if f_list and f_name not in f_list:
                issues.append(f"fluid interface '{f_name}' of the mapper is not in fluid_interfaces_list")
        if len({s for _, s in interface_pairs}) > 1:
            issues.append("all mappers must share one structure interface (the two faces of a "
                          "double-faced structure)")
        for label, names in (("fluid", [f for f, _ in interface_pairs] + list(f_list)),
                             ("structure", [s for _, s in interface_pairs] + list(s_list))):
            for name in dict.fromkeys(names):
                if roots[label] and not name.startswith(roots[label] + "."):
                    issues.append(f"{label} interface '{name}' must be a full model part name "
                                  f"'{roots[label]}.<SubModelPart>' (a bare name makes Kratos fail "
                                  "with 'not found as root-ModelPart')")
                elif label in available:
                    check_part(label, name, f"{label} interface")
                    nodes = _interface_nodes(meshes[label], _local(name, roots[label]))
                    if nodes is not None and not nodes:
                        issues.append(f"{label} interface '{name}' contains no nodes")
        # Interfaces must describe the same physical boundary at t=0.
        if len(interface_pairs) >= 1 and set(meshes) == {"fluid", "structure"}:
            f_nodes = _interface_nodes(meshes["fluid"], _local(interface_pairs[0][0], fluid_root))
            s_nodes = _interface_nodes(meshes["structure"], _local(interface_pairs[0][1], structure_root))
            if f_nodes and s_nodes:
                def bbox(mesh: mdpa_mod.Mdpa, ids: list[int]) -> list[tuple[float, float]]:
                    pts = [mesh.nodes[i] for i in ids]
                    return [(min(p[a] for p in pts), max(p[a] for p in pts)) for a in range(3)]
                fb, sb = bbox(meshes["fluid"], f_nodes), bbox(meshes["structure"], s_nodes)
                extent = max(hi - lo for lo, hi in fb + sb) or 1.0
                if any(abs(a - b) > 1e-6 * extent + 1e-9 for fa, sa in zip(fb, sb) for a, b in zip(fa, sa)):
                    warnings.append("the fluid and structure interfaces do not occupy the same "
                                    f"region at t=0 (fluid bbox {fb}, structure bbox {sb}): the "
                                    "mapper will extrapolate")
        max_it = coupling.get("nl_max_it", 25)
        if isinstance(max_it, bool) or not isinstance(max_it, int) or max_it < 1:
            issues.append(f"coupling_settings.nl_max_it must be a positive integer, got {max_it!r}")
        tol = coupling.get("nl_tol", 1e-8)
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or tol <= 0:
            issues.append(f"coupling_settings.nl_tol must be a positive number, got {tol!r}")
        strategy = (coupling.get("coupling_strategy_settings") or {}).get("solver_type")
        if strategy is not None and strategy not in ACCELERATORS:
            issues.append(f"coupling_strategy_settings.solver_type '{strategy}' is unknown "
                          f"(allowed: {list(ACCELERATORS)})")
        if "randomized_SVD" in str(strategy):
            warnings.append(f"'{strategy}' additionally requires LinearSolversApplication")

    if params.get("problem_data", {}).get("parallel_type", "OpenMP") != "OpenMP":
        issues.append("problem_data.parallel_type must be 'OpenMP' (MPI FSI is not supported by this server)")
    if "restart_settings" in params:
        issues.append("restart_settings: FSI restart is not implemented in Kratos' FsiAnalysis")

    return {"valid": not issues, "issues": issues, "warnings": warnings,
            "deep_validated": False, "required_applications": list(REQUIRED_APPLICATIONS)}


def summarize(params: dict[str, Any]) -> dict[str, Any]:
    """Structured summary for explain_project_parameters (``kind: "fsi"``)."""
    solver = params.get("solver_settings", {})
    coupling = solver.get("coupling_settings") or {}

    def sub(key: str) -> dict[str, Any]:
        b = solver.get(key) or {}
        return {"solver_type": b.get("solver_type"), "model_part_name": b.get("model_part_name"),
                "domain_size": b.get("domain_size"), "model_import": b.get("model_import_settings"),
                "materials": b.get("material_import_settings")}

    def listing(group: str) -> dict[str, int]:
        return {k: len(v) for k, v in (params.get(group) or {}).items() if isinstance(v, list)}

    problem = params.get("problem_data", {})
    return {
        "analysis_type": "fsi",
        "problem_data": {k: problem.get(k) for k in ("problem_name", "start_time", "end_time", "parallel_type")},
        "coupling_scheme": solver.get("coupling_scheme"),
        "fluid": sub("fluid_solver_settings"),
        "structure": sub("structure_solver_settings"),
        "mesh_motion": sub("mesh_solver_settings") if solver.get("mesh_solver_settings") else None,
        "coupling": {
            "accelerator": (coupling.get("coupling_strategy_settings") or {}).get("solver_type"),
            "nl_tol": coupling.get("nl_tol"), "nl_max_it": coupling.get("nl_max_it"),
            "solve_mesh_at_each_iteration": coupling.get("solve_mesh_at_each_iteration"),
            "mappers": coupling.get("mapper_settings"),
            "fluid_interfaces": coupling.get("fluid_interfaces_list"),
            "structure_interfaces": coupling.get("structure_interfaces_list"),
        },
        "processes": listing("processes"),
        "output_processes": listing("output_processes"),
        "required_applications": list(REQUIRED_APPLICATIONS),
    }


# --------------------------------------------------------------------------
# Generator behind create_fsi_project: a channel whose floor is an elastic block.
# --------------------------------------------------------------------------

FLUID_ROOT = "FluidModelPart"
STRUCTURE_ROOT = "Structure"
FLUID_INTERFACE = f"{FLUID_ROOT}.Interface"
STRUCTURE_INTERFACE = f"{STRUCTURE_ROOT}.Interface"


def _union(mesh: mdpa_mod.Mdpa, name: str, parts: list[str]) -> None:
    nodes: list[int] = []
    for p in parts:
        nodes.extend(mesh.sub_model_parts[p].nodes)
    mesh.sub_model_parts[name] = mdpa_mod.SubModelPart(name, nodes=sorted(set(nodes)))


def _rename(mesh: mdpa_mod.Mdpa, old: str, new: str) -> None:
    part = mesh.sub_model_parts.pop(old)
    part.name = new
    mesh.sub_model_parts[new] = part


def flexible_wall_meshes(length: float, fluid_height: float, wall_thickness: float,
                         fluid_divisions: tuple[int, int], wall_divisions: tuple[int, int]
                         ) -> tuple[mdpa_mod.Mdpa, mdpa_mod.Mdpa]:
    """(fluid, structure) meshes for a channel [0,L]x[0,H] sitting on an elastic
    block [0,L]x[-t,0]. The two interface discretizations may differ (the FSI
    mapper handles non-matching meshes)."""
    fluid = mdpa_mod.create_rectangle_mesh(
        length, fluid_height, *fluid_divisions, element_name="Element2D3N",
        condition_name="WallCondition2D2N", root_part="Parts_Fluid", triangles=True)
    for old, new in (("left", "Inlet"), ("right", "Outlet"), ("top", "NoSlip"), ("bottom", "Interface")):
        _rename(fluid, old, new)
    _union(fluid, "MeshFixed", ["Inlet", "Outlet", "NoSlip"])

    wall = mdpa_mod.create_rectangle_mesh(
        length, wall_thickness, *wall_divisions, element_name="TotalLagrangianElement2D4N",
        condition_name="LineCondition2D2N", root_part="Parts_Structure")
    wall.nodes = {i: (x, y - wall_thickness, z) for i, (x, y, z) in wall.nodes.items()}
    _rename(wall, "top", "Interface")
    _union(wall, "Fixed", ["left", "right", "bottom"])
    for part in ("left", "right", "bottom"):
        del wall.sub_model_parts[part]
    return fluid, wall


def flexible_wall_parameters(
    *, length: float, fluid_height: float, inlet_velocity: float, ramp_time: float,
    start_time: float, end_time: float, time_step: float, structure_static: bool,
    nl_tol: float, nl_max_it: int, accelerator: str, output_interval: int,
) -> dict[str, Any]:
    """ProjectParameters for the flexible-wall channel (files fluid.mdpa, structure.mdpa,
    MaterialsFluid.json, MaterialsStructure.json)."""
    profile = (f"{inlet_velocity}*4*y*({fluid_height}-y)/{fluid_height}**2"
               f"*0.5*(1-cos(pi*t/{ramp_time}))")
    steady = f"{inlet_velocity}*4*y*({fluid_height}-y)/{fluid_height}**2"

    def vtk(model_part: str, name: str, variables: list[str]) -> dict[str, Any]:
        return {"python_module": "vtk_output_process", "kratos_module": "KratosMultiphysics",
                "process_name": "VtkOutputProcess", "Parameters": {
                    "model_part_name": model_part, "output_control_type": "step",
                    "output_interval": output_interval, "file_format": "ascii",
                    "output_precision": 7, "output_sub_model_parts": False,
                    "output_path": f"vtk_output/{name}", "save_output_files_in_folder": True,
                    "nodal_solution_step_data_variables": variables,
                    "nodal_data_value_variables": [], "element_data_value_variables": [],
                    "condition_data_value_variables": []}}

    def assign(module: str, mp: str, variable: str, **extra: Any) -> dict[str, Any]:
        return {"python_module": module, "kratos_module": "KratosMultiphysics",
                "process_name": {"assign_vector_variable_process": "AssignVectorVariableProcess",
                                 "assign_vector_by_direction_process": "AssignVectorByDirectionProcess",
                                 "assign_scalar_variable_process": "AssignScalarVariableProcess"}[module],
                "Parameters": {"model_part_name": mp, "variable_name": variable,
                               "interval": [0.0, "End"], **extra}}

    structure_solver: dict[str, Any] = {
        "solver_type": "Static" if structure_static else "Dynamic",
        "model_part_name": STRUCTURE_ROOT, "domain_size": 2, "echo_level": 0,
        "analysis_type": "linear" if structure_static else "non_linear",
        "model_import_settings": {"input_type": "mdpa", "input_filename": "structure"},
        "material_import_settings": {"materials_filename": "MaterialsStructure.json"},
        "time_stepping": {"time_step": time_step},
        "convergence_criterion": "residual_criterion",
        "displacement_relative_tolerance": 1e-6, "displacement_absolute_tolerance": 1e-9,
        "residual_relative_tolerance": 1e-6, "residual_absolute_tolerance": 1e-9,
        "max_iteration": 10,
        "problem_domain_sub_model_part_list": ["Parts_Structure"],
        "processes_sub_model_part_list": ["Fixed", "Interface"],
        "rotation_dofs": False,
    }
    if not structure_static:
        structure_solver.update(time_integration_method="implicit", scheme_type="bossak")
    return {
        "problem_data": {"problem_name": "fsi_flexible_wall", "parallel_type": "OpenMP",
                         "echo_level": 0, "start_time": start_time, "end_time": end_time},
        "solver_settings": {
            "solver_type": "partitioned", "coupling_scheme": "dirichlet_neumann", "echo_level": 1,
            "structure_solver_settings": structure_solver,
            "fluid_solver_settings": {
                "solver_type": "Monolithic", "model_part_name": FLUID_ROOT, "domain_size": 2,
                "model_import_settings": {"input_type": "mdpa", "input_filename": "fluid"},
                "material_import_settings": {"materials_filename": "MaterialsFluid.json"},
                "echo_level": 0, "compute_reactions": True, "maximum_iterations": 10,
                "relative_velocity_tolerance": 1e-6, "absolute_velocity_tolerance": 1e-8,
                "relative_pressure_tolerance": 1e-6, "absolute_pressure_tolerance": 1e-8,
                "volume_model_part_name": f"{FLUID_ROOT}.Parts_Fluid",
                "skin_parts": [f"{FLUID_ROOT}.{p}" for p in ("Inlet", "Outlet", "NoSlip", "Interface")],
                "no_skin_parts": [f"{FLUID_ROOT}.MeshFixed"],
                "time_stepping": {"automatic_time_step": False, "time_step": time_step},
                "formulation": {"element_type": "vms", "dynamic_tau": 1.0},
            },
            "mesh_solver_settings": {
                "echo_level": 0, "domain_size": 2, "model_part_name": FLUID_ROOT,
                "solver_type": "mesh_solver_structural_similarity"},
            "coupling_settings": {
                "nl_tol": nl_tol, "nl_max_it": nl_max_it, "solve_mesh_at_each_iteration": True,
                "coupling_strategy_settings": {"solver_type": accelerator, "w_0": 0.825, "buffer_size": 5},
                "structure_interfaces_list": [STRUCTURE_INTERFACE],
                "fluid_interfaces_list": [FLUID_INTERFACE],
                "mapper_settings": [{"mapper_face": "unique",
                                     "fluid_interface_submodelpart_name": FLUID_INTERFACE,
                                     "structure_interface_submodelpart_name": STRUCTURE_INTERFACE}],
            },
        },
        "processes": {
            "structure_constraints_process_list": [assign(
                "assign_vector_variable_process", f"{STRUCTURE_ROOT}.Fixed", "DISPLACEMENT",
                value=[0.0, 0.0, 0.0], constrained=[True, True, True])],
            "structure_loads_process_list": [],
            "fluid_initial_conditions_process_list": [],
            "fluid_boundary_conditions_process_list": [
                assign("assign_vector_by_direction_process", f"{FLUID_ROOT}.Inlet", "VELOCITY",
                       modulus=profile, constrained=True, direction="automatic_inwards_normal",
                       **{"interval": [0.0, ramp_time]}),
                assign("assign_vector_by_direction_process", f"{FLUID_ROOT}.Inlet", "VELOCITY",
                       modulus=steady, constrained=True, direction="automatic_inwards_normal",
                       **{"interval": [ramp_time, "End"]}),
                assign("assign_scalar_variable_process", f"{FLUID_ROOT}.Outlet", "PRESSURE",
                       constrained=True, value=0.0),
                {"python_module": "apply_noslip_process", "kratos_module": "KratosMultiphysics.FluidDynamicsApplication",
                 "process_name": "ApplyNoSlipProcess", "Parameters": {"model_part_name": f"{FLUID_ROOT}.NoSlip"}},
                assign("assign_vector_variable_process", f"{FLUID_ROOT}.MeshFixed", "MESH_DISPLACEMENT",
                       value=[0.0, 0.0, 0.0], constrained=[True, True, True]),
            ],
            "fluid_gravity": [],
            "fluid_auxiliar_process_list": [],
        },
        "output_processes": {
            "gid_output": [],
            "vtk_output": [
                vtk(f"{FLUID_ROOT}.Parts_Fluid", "fluid", ["VELOCITY", "PRESSURE", "MESH_DISPLACEMENT"]),
                vtk(f"{STRUCTURE_ROOT}.Parts_Structure", "structure", ["DISPLACEMENT", "REACTION"]),
            ],
        },
    }


def flexible_wall_materials(*, fluid_density: float, fluid_viscosity: float, young_modulus: float,
                            poisson_ratio: float, wall_density: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """(fluid, structure) materials files of the flexible-wall channel."""
    fluid = {"properties": [{
        "model_part_name": f"{FLUID_ROOT}.Parts_Fluid", "properties_id": 1,
        "Material": {"constitutive_law": {"name": "Newtonian2DLaw"},
                     "Variables": {"DENSITY": fluid_density, "DYNAMIC_VISCOSITY": fluid_viscosity},
                     "Tables": {}}}]}
    structure = {"properties": [{
        "model_part_name": f"{STRUCTURE_ROOT}.Parts_Structure", "properties_id": 1,
        "Material": {"constitutive_law": {"name": "LinearElasticPlaneStrain2DLaw"},
                     "Variables": {"YOUNG_MODULUS": young_modulus, "POISSON_RATIO": poisson_ratio,
                                   "DENSITY": wall_density, "THICKNESS": 1.0},
                     "Tables": {}}}]}
    return fluid, structure
