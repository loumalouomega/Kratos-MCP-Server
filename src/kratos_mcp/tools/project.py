"""ProjectParameters / Materials editing tools: read, set and delete settings,
list/add/update/remove processes, update solver settings, edit materials.

Complements the scaffolding tools (which create files and append a fixed set of
boundary-condition blocks) with generic, stage-aware editing. Pure Python; the
checks that need a Kratos build (variable names, constitutive laws) run through
the cached worker bridge and degrade to a note when no build is available."""

from __future__ import annotations

import copy
import functools
import inspect
from pathlib import Path
from typing import Any, Callable

import anyio

from .. import bridge, kratos_env, mdpa as mdpa_mod, project_edit as pe
from .scaffold import _case_model_parts, load_linear_solver_presets, load_material_presets

VARIABLE_KEYS = ("variable_name", "output_variables", "nodal_solution_step_data_variables",
                 "nodal_data_value_variables", "element_data_value_variables",
                 "condition_data_value_variables", "gauss_point_variables_in_elements",
                 "gauss_point_variables_extrapolated_to_nodes")


def _errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn ValueError (our validation) and OSError into {"error": ...}."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except (ValueError, OSError) as exc:
                return {"error": str(exc)}

        return awrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError) as exc:
            return {"error": str(exc)}

    return wrapper


def _path(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _known_variables() -> set[str] | None:
    """Every variable name of the configured build, or None without one."""
    try:
        if not kratos_env.is_available():
            return None
        listed = bridge.run_op("list_variables")
    except Exception:  # noqa: BLE001 - best effort, the edit must not depend on it
        return None
    return {name for names in listed.values() for name in names}


def _unknown_variables(block_params: dict[str, Any], known: set[str]) -> list[str]:
    bad: list[str] = []
    for key in VARIABLE_KEYS:
        value = block_params.get(key)
        names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        for name in names:
            if not isinstance(name, str) or name in known:
                continue
            if name[-2:] in ("_X", "_Y", "_Z") and name[:-2] in known:
                continue
            bad.append(name)
    return sorted(set(bad))


def _model_part_warnings(parameters_file: Path, params: dict[str, Any], stage: str | None,
                         block_params: dict[str, Any]) -> list[str]:
    if stage is not None or "stages" in params:
        return []
    refs = {v for k, v in block_params.items() if "model_part_name" in k and isinstance(v, str)}
    info = _case_model_parts(parameters_file)
    if info is None or not refs:
        return []
    root, paths = info
    return [f"{ref}: not the root model part '{root}' nor '{root}.<sub model part>' of the mesh "
            f"(sub model parts: {sorted(paths)})"
            for ref in sorted(refs) if not pe.check_model_part_ref(ref, root, paths)]


def register(mcp) -> None:

    # ------------------------------------------------------------- settings

    @mcp.tool()
    @_errors
    def project_get_setting(parameters_file: str, pointer: str = "",
                            stage: str | None = None) -> dict[str, Any]:
        """Read any part of a ProjectParameters.json by JSON Pointer, e.g.
        '/solver_settings/time_stepping/time_step' or '/processes/loads_process_list/0'
        ('' = the whole file or stage). For multi-stage (orchestrator) files pass
        stage; the pointer is then relative to that stage's stage_settings."""
        path = _path(parameters_file)
        sc = pe.scope(pe.load_json(path), stage)
        return {"pointer": pointer, "stage": stage, "value": pe.pointer_get(sc, pointer)}

    @mcp.tool()
    @_errors
    def project_set_setting(parameters_file: str, pointer: str, value: Any,
                            stage: str | None = None, create: bool = True) -> dict[str, Any]:
        """Set (or add) any value in a ProjectParameters.json by JSON Pointer,
        e.g. pointer '/solver_settings/time_stepping/time_step' value 0.05, or
        '/processes/loads_process_list/-' to append a whole process block to a
        list. Missing intermediate objects are created when create=true. Returns
        the previous value. For multi-stage files pass stage (pointer relative to
        its stage_settings). The file is rewritten atomically."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        sc = pe.scope(params, stage)
        old = pe.pointer_set(sc, pointer, value, create)
        pe.save_json(path, params)
        return {"written_to": str(path), "pointer": pointer, "previous": old, "value": value}

    @mcp.tool()
    @_errors
    def project_delete_setting(parameters_file: str, pointer: str,
                               stage: str | None = None) -> dict[str, Any]:
        """Delete a key (or list item by index) from a ProjectParameters.json by
        JSON Pointer. Returns the removed value."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        removed = pe.pointer_delete(pe.scope(params, stage), pointer)
        pe.save_json(path, params)
        return {"written_to": str(path), "pointer": pointer, "removed": removed}

    # ------------------------------------------------------------ processes

    @mcp.tool()
    @_errors
    def project_list_processes(parameters_file: str, stage: str | None = None) -> dict[str, Any]:
        """List every process block of a ProjectParameters.json (all lists of
        'processes' and 'output_processes') with its section, list name, index,
        python_module, model_part_name, variable_name and interval. The
        (list, index) pair addresses a block in project_update_process /
        project_remove_process."""
        path = _path(parameters_file)
        sc = pe.scope(pe.load_json(path), stage)
        found = pe.list_processes(sc)
        return {"count": len(found), "processes": found,
                "lists": {section: sorted((sc.get(section) or {})) for section in pe.PROCESS_SECTIONS}}

    @mcp.tool()
    @_errors
    async def project_add_process(
        parameters_file: str,
        python_module: str,
        parameters: dict[str, Any] | None = None,
        process_list: str | None = None,
        section: str | None = None,
        kratos_module: str | None = None,
        process_name: str | None = None,
        position: int | None = None,
        merge_defaults: bool = True,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """Add ANY Kratos process to a ProjectParameters.json -- the generic
        counterpart of add_boundary_condition / add_output_process. python_module
        is a module name from kratos_list_processes (e.g.
        'assign_scalar_variable_process', 'apply_constraint_vector_process',
        'compute_...'); 'parameters' are its Parameters (see
        kratos_get_process_defaults for the schema). With merge_defaults (default)
        the process' real default settings fill every key you omit (needs the
        Kratos source tree). kratos_module is inferred ('KratosMultiphysics' for
        core, 'KratosMultiphysics.<App>' for application processes; pass it
        explicitly if a warning says it was guessed). The block goes into
        process_list of 'section' ('processes' by default, 'output_processes' for
        vtk/gid output modules), created if missing, at 'position' (default:
        end). Warnings flag model parts missing from the mesh and, with a Kratos
        build, unknown variable names."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        sc = pe.scope(params, stage)
        block, warnings = pe.build_process_block(python_module, parameters, kratos_module,
                                                 process_name, merge_defaults)
        default_section, default_list = pe.default_target(python_module)
        section = section or default_section
        process_list_name = process_list or (default_list if section == default_section
                                             else "list_other_processes")
        target = pe.process_list(sc, section, process_list_name, create=True)
        warnings += _model_part_warnings(path, params, stage, block["Parameters"])
        known = await anyio.to_thread.run_sync(_known_variables)
        if known is not None:
            warnings += [f"Unknown variable '{name}' in this Kratos build"
                         for name in _unknown_variables(block["Parameters"], known)]
        if position is None:
            target.append(block)
            index = len(target) - 1
        else:
            if not 0 <= position <= len(target):
                raise ValueError(f"position must be between 0 and {len(target)}")
            target.insert(position, block)
            index = position
        pe.save_json(path, params)
        return {"written_to": str(path), "section": section, "list": process_list_name,
                "index": index, "added": block, "warnings": warnings}

    def _select(sc: dict[str, Any], section, process_list, index, python_module,
                model_part_name, variable_name, many: bool):
        matches = pe.match_processes(sc, section, process_list, index, python_module,
                                     model_part_name, variable_name)
        if not matches:
            raise ValueError("No process matches; see project_list_processes")
        if len(matches) > 1 and not many:
            raise ValueError(f"{len(matches)} processes match ({matches[:6]}); narrow it with "
                             "process_list+index or more criteria")
        return matches

    @mcp.tool()
    @_errors
    def project_update_process(
        parameters_file: str,
        parameters: dict[str, Any],
        process_list: str | None = None,
        index: int | None = None,
        section: str | None = None,
        python_module: str | None = None,
        model_part_name: str | None = None,
        variable_name: str | None = None,
        replace: bool = False,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """Change the Parameters of one existing process block. Address it by
        process_list + index (from project_list_processes) or by matching
        python_module / model_part_name / variable_name (must match exactly one
        block). 'parameters' is deep-merged into the block's Parameters (nested
        objects merge, everything else replaces); replace=true swaps the whole
        Parameters object instead."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        sc = pe.scope(params, stage)
        (sec, name, idx), = _select(sc, section, process_list, index, python_module,
                                    model_part_name, variable_name, many=False)
        block = sc[sec][name][idx]
        if replace:
            block["Parameters"] = copy.deepcopy(parameters)
        else:
            pe.deep_merge(block.setdefault("Parameters", {}), parameters)
        pe.save_json(path, params)
        return {"written_to": str(path), "section": sec, "list": name, "index": idx,
                "updated": block}

    @mcp.tool()
    @_errors
    def project_remove_process(
        parameters_file: str,
        process_list: str | None = None,
        index: int | None = None,
        section: str | None = None,
        python_module: str | None = None,
        model_part_name: str | None = None,
        variable_name: str | None = None,
        all_matches: bool = False,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """Remove process blocks, addressed by process_list + index or by
        matching python_module / model_part_name / variable_name. More than one
        match is refused unless all_matches=true. Returns the removed blocks."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        sc = pe.scope(params, stage)
        matches = _select(sc, section, process_list, index, python_module, model_part_name,
                          variable_name, many=all_matches)
        removed = []
        for sec, name, idx in sorted(matches, key=lambda m: (m[0], m[1], -m[2])):
            removed.append(sc[sec][name].pop(idx))
        pe.save_json(path, params)
        return {"written_to": str(path), "removed_count": len(removed), "removed": removed}

    # --------------------------------------------------------------- solver

    @mcp.tool()
    @_errors
    def project_update_solver(
        parameters_file: str,
        settings: dict[str, Any] | None = None,
        linear_solver_preset: str | None = None,
        linear_solver_settings: dict[str, Any] | None = None,
        time_step: float | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        domain_size: int | None = None,
        mesh_file: str | None = None,
        materials_file: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """Update solver_settings / problem_data of a ProjectParameters.json.
        'settings' is deep-merged into solver_settings (e.g. {"solver_type":
        "Dynamic", "analysis_type": "non_linear", "residual_relative_tolerance":
        1e-6}). linear_solver_preset applies a block from
        list_linear_solver_presets (sparse_lu, amgcl, cg, ...), or pass an
        explicit linear_solver_settings. time_step sets
        solver_settings.time_stepping.time_step; start_time / end_time live in
        problem_data. domain_size sets solver_settings.domain_size. mesh_file
        points model_import_settings at an .mdpa (input_type 'mdpa', file name
        without extension, relative to the case directory) and materials_file
        sets material_import_settings.materials_filename. Returns what changed.
        Run validate_project_parameters(deep) afterwards to check the solver
        accepts it."""
        path = _path(parameters_file)
        params = pe.load_json(path)
        sc = pe.scope(params, stage)
        solver = sc.setdefault("solver_settings", {})
        changed: dict[str, Any] = {}
        if settings:
            pe.deep_merge(solver, settings)
            changed["solver_settings"] = settings
        if linear_solver_preset and linear_solver_settings:
            raise ValueError("give linear_solver_preset or linear_solver_settings, not both")
        if linear_solver_preset:
            presets = load_linear_solver_presets()
            if linear_solver_preset not in presets:
                raise ValueError(f"Unknown preset '{linear_solver_preset}'. "
                                 f"Available: {sorted(presets)}")
            solver["linear_solver_settings"] = copy.deepcopy(presets[linear_solver_preset]["settings"])
            changed["linear_solver_settings"] = solver["linear_solver_settings"]
        if linear_solver_settings:
            solver["linear_solver_settings"] = copy.deepcopy(linear_solver_settings)
            changed["linear_solver_settings"] = linear_solver_settings
        if time_step is not None:
            if time_step <= 0:
                raise ValueError("time_step must be positive")
            solver.setdefault("time_stepping", {})["time_step"] = time_step
            changed["time_step"] = time_step
        problem = sc.setdefault("problem_data", {})
        for key, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None:
                problem[key] = value
                changed[key] = value
        if problem.get("end_time") is not None and problem.get("start_time") is not None \
                and problem["end_time"] <= problem["start_time"]:
            raise ValueError("end_time must be greater than start_time")
        if domain_size is not None:
            if domain_size not in (2, 3):
                raise ValueError("domain_size must be 2 or 3")
            solver["domain_size"] = domain_size
            changed["domain_size"] = domain_size
        if mesh_file:
            stem = mesh_file[:-5] if mesh_file.endswith(".mdpa") else mesh_file
            solver["model_import_settings"] = {"input_type": "mdpa", "input_filename": stem}
            changed["model_import_settings"] = solver["model_import_settings"]
        if materials_file:
            solver.setdefault("material_import_settings", {})["materials_filename"] = materials_file
            changed["materials_filename"] = materials_file
        if not changed:
            raise ValueError("nothing to change; pass at least one argument besides parameters_file")
        pe.save_json(path, params)
        return {"written_to": str(path), "stage": stage, "changed": changed}

    # ------------------------------------------------------------ materials

    @mcp.tool()
    @_errors
    def materials_get(materials_file: str) -> dict[str, Any]:
        """Read a Materials.json: one summary row per properties entry
        (model_part_name, properties_id, constitutive law, variable names) plus
        the raw document."""
        doc = pe.load_json(_path(materials_file))
        rows = [{"model_part_name": e.get("model_part_name"),
                 "properties_id": e.get("properties_id"),
                 "constitutive_law": (e.get("Material", {}).get("constitutive_law") or {}).get("name"),
                 "variables": sorted(e.get("Material", {}).get("Variables", {})),
                 "tables": sorted(e.get("Material", {}).get("Tables", {}))}
                for e in pe.material_entries(doc)]
        return {"count": len(rows), "entries": rows, "document": doc}

    @mcp.tool()
    @_errors
    def materials_set_property(
        materials_file: str,
        model_part_name: str,
        properties_id: int | None = None,
        preset: str | None = None,
        constitutive_law: str | None = None,
        variables: dict[str, Any] | None = None,
        tables: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Add or update ONE entry of a Materials.json (created if the file does
        not exist), keyed by model_part_name (e.g. 'Structure.domain'). New entries
        get the next free properties_id unless given. 'preset' (see
        list_material_presets) fills the constitutive law and default variables;
        'constitutive_law' and 'variables' then override. On an existing entry,
        variables/tables are merged into the current ones; replace=true rebuilds
        the entry from scratch. This is the incremental counterpart of
        create_materials, which rewrites the whole file."""
        path = _path(materials_file)
        doc = pe.load_json(path) if path.is_file() else pe.empty_materials()
        merged_vars: dict[str, Any] = {}
        law = constitutive_law
        if preset:
            presets = load_material_presets()
            if preset not in presets:
                raise ValueError(f"Unknown preset '{preset}'. Available: {sorted(presets)}")
            law = law or presets[preset].get("constitutive_law")
            merged_vars.update(presets[preset].get("variables", {}))
        merged_vars.update(variables or {})
        entry, created = pe.upsert_material(doc, model_part_name, properties_id, law,
                                            merged_vars, tables, replace)
        pe.save_json(path, doc)
        return {"written_to": str(path), "created": created, "entry": entry}

    @mcp.tool()
    @_errors
    def materials_remove_property(materials_file: str, model_part_name: str | None = None,
                                  properties_id: int | None = None) -> dict[str, Any]:
        """Remove entries from a Materials.json matching model_part_name and/or
        properties_id (all given criteria must match)."""
        path = _path(materials_file)
        doc = pe.load_json(path)
        removed = pe.remove_material(doc, model_part_name, properties_id)
        pe.save_json(path, doc)
        return {"written_to": str(path), "removed": removed,
                "remaining": len(pe.material_entries(doc))}

    @mcp.tool()
    @_errors
    async def materials_check(parameters_file: str, applications: list[str] | None = None
                              ) -> dict[str, Any]:
        """Cross-check a case's Materials.json against its mesh: every entry's
        model_part_name must be the root model part or one of the mesh's sub model
        parts, every properties_id used by an element needs an entry, and (with a
        Kratos build) every constitutive law must exist -- pass the application
        names that register them (e.g. ["StructuralMechanicsApplication"]) so they
        are imported first. Single-stage cases only."""
        pfile = _path(parameters_file)
        params = pe.load_json(pfile)
        if "stages" in params:
            raise ValueError("materials_check supports single-stage files")
        solver = params.get("solver_settings", {})
        mat_name = solver.get("material_import_settings", {}).get("materials_filename")
        if not mat_name:
            raise ValueError("solver_settings.material_import_settings.materials_filename is not set")
        doc = pe.load_json(pfile.parent / mat_name)
        entries = pe.material_entries(doc)
        issues: list[str] = []
        notes: list[str] = []
        info = _case_model_parts(pfile)
        used_ids: set[int] = set()
        if info is None:
            notes.append("Mesh could not be read: model part and property id checks skipped")
        else:
            root, paths = info
            for e in entries:
                name = e.get("model_part_name", "")
                if not pe.check_model_part_ref(name, root, paths):
                    issues.append(f"model_part_name '{name}' is not in the mesh "
                                  f"(root '{root}', parts {sorted(paths)})")
            import_name = solver["model_import_settings"]["input_filename"]
            mesh = mdpa_mod.read(pfile.parent / (import_name if import_name.endswith(".mdpa")
                                                 else f"{import_name}.mdpa"))
            used_ids = {pid for by_id in mesh.elements.values() for pid, _ in by_id.values()}
            given = {e.get("properties_id") for e in entries}
            issues += [f"Elements use properties_id {pid} but Materials.json has no such entry"
                       for pid in sorted(used_ids - given)]
            notes += [f"Entry properties_id {pid} is not used by any element"
                      for pid in sorted(given - used_ids) if pid is not None]
        laws = sorted({(e.get("Material", {}).get("constitutive_law") or {}).get("name")
                       for e in entries} - {None})
        if laws:
            try:
                present = await anyio.to_thread.run_sync(
                    lambda: bridge.run_op("has_constitutive_laws",
                                          {"names": laws, "applications": applications or []},
                                          use_cache=False))
                issues += [f"Constitutive law '{n}' is not registered in this Kratos build"
                           + ("" if applications else " (pass applications=[...] if it comes "
                                                      "from an application)")
                           for n, ok in present.items() if not ok]
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Constitutive law check skipped (no usable Kratos build: {exc})")
        return {"valid": not issues, "issues": issues, "notes": notes,
                "entries": len(entries), "constitutive_laws": laws}
