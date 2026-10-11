"""Kratos-free editing helpers for ProjectParameters.json and Materials.json.

The tools in ``tools/project.py`` are thin wrappers around this module. All
functions raise ``ValueError`` with a readable message on bad input.

Single-stage files keep ``problem_data`` / ``solver_settings`` / ``processes`` /
``output_processes`` at the top level; multi-stage (orchestrator) files keep
them under ``stages.<name>.stage_settings``. :func:`scope` resolves which
dictionary an edit applies to.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import process_catalog, source_catalog

PROCESS_SECTIONS = ("processes", "output_processes")
OUTPUT_MODULES = ("vtk_output_process", "gid_output_process", "json_output_process",
                  "point_output_process", "multiple_points_output_process",
                  "compare_two_files_check_process")
# Core process modules that need no application prefix (used when the Kratos
# source tree is unavailable to look the module up).
CORE_MODULES = {
    "assign_scalar_variable_process", "assign_vector_variable_process",
    "assign_vector_by_direction_process", "assign_scalar_input_to_entities_process",
    "assign_vector_by_direction_to_condition_process", "assign_scalar_variable_to_conditions_process",
    "assign_vector_variable_to_conditions_process", "assign_scalar_variable_to_entities_process",
    "assign_vector_variable_to_entities_process", "vtk_output_process", "json_output_process",
    "point_output_process", "from_json_check_result_process", "apply_constraint_vector_process",
    "apply_constraint_scalar_process", "assign_vector_component_variable_process",
}


# --------------------------------------------------------------------- file I/O

def _strip_comments(text: str) -> str:
    """Remove ``//`` comments that are not inside JSON strings (Kratos accepts them)."""
    out, in_str, escape, i = [], False, False, 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch == "/" and text[i:i + 2] == "//":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ValueError(f"{path} does not exist")
    text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return json.loads(_strip_comments(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from None


def save_json(path: Path, data: Any, indent: int = 4) -> None:
    """Atomic write (temp file + rename) so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=indent)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------- scopes

def scope(params: dict[str, Any], stage: str | None = None) -> dict[str, Any]:
    """The dict holding solver_settings / processes for the file or the stage."""
    if "stages" in params:
        stages = params["stages"]
        if stage is None:
            raise ValueError(f"This is a multi-stage file; pass stage= one of {sorted(stages)}")
        if stage not in stages:
            raise ValueError(f"Unknown stage '{stage}'. Stages: {sorted(stages)}")
        return stages[stage].setdefault("stage_settings", {})
    if stage is not None:
        raise ValueError("stage was given but the file has no 'stages' (single-stage file)")
    return params


# --------------------------------------------------------------- JSON pointers

def _tokens(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def pointer_get(doc: Any, pointer: str) -> Any:
    node = doc
    for token in _tokens(pointer):
        if isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                raise ValueError(f"{pointer}: no list index '{token}'") from None
        elif isinstance(node, dict):
            if token not in node:
                raise ValueError(f"{pointer}: no key '{token}' (keys: {sorted(node)[:20]})")
            node = node[token]
        else:
            raise ValueError(f"{pointer}: cannot descend into a {type(node).__name__} at '{token}'")
    return node


def _parent(doc: Any, pointer: str, create: bool) -> tuple[Any, str]:
    tokens = _tokens(pointer)
    if not tokens:
        raise ValueError("The root cannot be replaced or removed; point at a key")
    node = doc
    for token in tokens[:-1]:
        if isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                raise ValueError(f"{pointer}: no list index '{token}'") from None
        elif isinstance(node, dict):
            if token not in node:
                if not create:
                    raise ValueError(f"{pointer}: no key '{token}'")
                node[token] = {}
            node = node[token]
        else:
            raise ValueError(f"{pointer}: cannot descend into a {type(node).__name__} at '{token}'")
    return node, tokens[-1]


def pointer_set(doc: Any, pointer: str, value: Any, create: bool = True) -> Any:
    """Set (or add) the value at ``pointer``; returns the previous value or None.
    In a list, ``-`` appends and an integer index replaces."""
    parent, last = _parent(doc, pointer, create)
    if isinstance(parent, list):
        if last == "-":
            parent.append(value)
            return None
        try:
            index = int(last)
            old = parent[index]
        except (ValueError, IndexError):
            raise ValueError(f"{pointer}: no list index '{last}'") from None
        parent[index] = value
        return old
    if isinstance(parent, dict):
        old = parent.get(last)
        if last not in parent and not create:
            raise ValueError(f"{pointer}: key '{last}' does not exist")
        parent[last] = value
        return old
    raise ValueError(f"{pointer}: parent is a {type(parent).__name__}")


def pointer_delete(doc: Any, pointer: str) -> Any:
    parent, last = _parent(doc, pointer, create=False)
    if isinstance(parent, list):
        try:
            return parent.pop(int(last))
        except (ValueError, IndexError):
            raise ValueError(f"{pointer}: no list index '{last}'") from None
    if isinstance(parent, dict):
        if last not in parent:
            raise ValueError(f"{pointer}: no key '{last}'")
        return parent.pop(last)
    raise ValueError(f"{pointer}: parent is a {type(parent).__name__}")


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``update`` into ``base`` (dicts merge, everything else replaces)."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


# -------------------------------------------------------------------- processes

def list_processes(sc: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat description of every process block in the scope."""
    found: list[dict[str, Any]] = []
    for section in PROCESS_SECTIONS:
        for list_name, blocks in (sc.get(section) or {}).items():
            if not isinstance(blocks, list):
                continue
            for index, block in enumerate(blocks):
                params = block.get("Parameters", {}) if isinstance(block, dict) else {}
                entry = {"section": section, "list": list_name, "index": index,
                         "python_module": block.get("python_module"),
                         "process_name": block.get("process_name"),
                         "model_part_name": params.get("model_part_name"),
                         "variable_name": params.get("variable_name"),
                         "interval": params.get("interval")}
                found.append({k: v for k, v in entry.items() if v is not None})
    return found


def process_list(sc: dict[str, Any], section: str, list_name: str, create: bool = False
                 ) -> list[dict[str, Any]]:
    if section not in PROCESS_SECTIONS:
        raise ValueError(f"section must be one of {list(PROCESS_SECTIONS)}")
    holder = sc.get(section)
    if holder is None:
        if not create:
            raise ValueError(f"No '{section}' in the file")
        holder = sc[section] = {}
    if list_name not in holder:
        if not create:
            raise ValueError(f"No process list '{list_name}' in '{section}'. "
                             f"Lists: {sorted(holder)}")
        holder[list_name] = []
    return holder[list_name]


def default_target(python_module: str) -> tuple[str, str]:
    """Where a new block of this module usually goes (section, list name)."""
    if python_module in OUTPUT_MODULES and python_module in ("vtk_output_process",
                                                              "gid_output_process"):
        return "output_processes", python_module.replace("_process", "")
    return "processes", "list_other_processes"


def camel(module: str) -> str:
    return "".join(part.capitalize() for part in module.split("_"))


def infer_kratos_module(python_module: str) -> tuple[str, bool]:
    """(kratos_module, certain). Looks the module up in the Kratos source when
    available; otherwise core modules map to KratosMultiphysics and anything
    else is a guess (certain=False)."""
    for item in source_catalog.list_python_processes():
        if item["module"] == python_module:
            app = item["application"]
            return ("KratosMultiphysics" if app == "Core" else f"KratosMultiphysics.{app}"), True
    if python_module in CORE_MODULES:
        return "KratosMultiphysics", True
    return "KratosMultiphysics", False


def build_process_block(python_module: str, parameters: dict[str, Any] | None = None,
                        kratos_module: str | None = None, process_name: str | None = None,
                        merge_defaults: bool = True) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not python_module or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", python_module):
        raise ValueError("python_module must be a Python module name such as "
                         "'assign_scalar_variable_process'")
    if kratos_module is None:
        kratos_module, certain = infer_kratos_module(python_module)
        if not certain:
            warnings.append(f"kratos_module defaulted to '{kratos_module}' for "
                            f"'{python_module}'; pass kratos_module=KratosMultiphysics.<App> if "
                            "the process belongs to an application")
    block: dict[str, Any] = {
        "python_module": python_module,
        "kratos_module": kratos_module,
        "process_name": process_name or camel(python_module),
        "Parameters": copy.deepcopy(parameters or {}),
    }
    if merge_defaults:
        info = process_catalog.get_process_defaults(python_module)
        if info:
            for key, value in info["default_settings"].items():
                if key != "help":
                    block["Parameters"].setdefault(key, copy.deepcopy(value))
        else:
            warnings.append("Process defaults unavailable (no Kratos source tree or "
                            "unparseable module): only the given Parameters were written")
    return block, warnings


def match_processes(sc: dict[str, Any], section: str | None = None, list_name: str | None = None,
                    index: int | None = None, python_module: str | None = None,
                    model_part_name: str | None = None, variable_name: str | None = None
                    ) -> list[tuple[str, str, int]]:
    """(section, list, index) of every block matching all given criteria."""
    if index is not None and (list_name is None):
        raise ValueError("index needs list_name (see project_list_processes)")
    matches = []
    for entry in list_processes(sc):
        if section and entry["section"] != section:
            continue
        if list_name and entry["list"] != list_name:
            continue
        if index is not None and entry["index"] != index:
            continue
        if python_module and entry.get("python_module") != python_module:
            continue
        if model_part_name and entry.get("model_part_name") != model_part_name:
            continue
        if variable_name and entry.get("variable_name") != variable_name:
            continue
        matches.append((entry["section"], entry["list"], entry["index"]))
    return matches


def check_model_part_ref(name: str, root: str | None, paths: set[str]) -> bool:
    """True when ``name`` is the root model part or ``root.<sub model part path>``."""
    if root is None or name == root:
        return True
    prefix = f"{root}."
    return name.startswith(prefix) and name[len(prefix):] in paths


# --------------------------------------------------------------------- materials

def empty_materials() -> dict[str, Any]:
    return {"properties": []}


def material_entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    entries = doc.get("properties")
    if not isinstance(entries, list):
        raise ValueError("Materials file has no 'properties' list")
    return entries


def upsert_material(doc: dict[str, Any], model_part_name: str, properties_id: int | None = None,
                    constitutive_law: str | None = None, variables: dict[str, Any] | None = None,
                    tables: dict[str, Any] | None = None, replace: bool = False
                    ) -> tuple[dict[str, Any], bool]:
    """Add or update the entry for ``model_part_name``; returns (entry, created).
    Updates merge Variables/Tables unless ``replace`` (which rebuilds the entry)."""
    entries = material_entries(doc)
    entry = next((e for e in entries if e.get("model_part_name") == model_part_name
                  and (properties_id is None or e.get("properties_id") == properties_id)), None)
    created = entry is None
    if created:
        used = {e.get("properties_id") for e in entries}
        pid = properties_id if properties_id is not None else \
            next(i for i in range(1, len(used) + 2) if i not in used)
        if pid in used:
            raise ValueError(f"properties_id {pid} is already used by another entry")
        entry = {"model_part_name": model_part_name, "properties_id": pid,
                 "Material": {"Variables": {}, "Tables": {}}}
        entries.append(entry)
    elif replace:
        entry["Material"] = {"Variables": {}, "Tables": {}}
    material = entry.setdefault("Material", {"Variables": {}, "Tables": {}})
    if constitutive_law:
        material["constitutive_law"] = {"name": constitutive_law}
    material.setdefault("Variables", {}).update(copy.deepcopy(variables or {}))
    material.setdefault("Tables", {}).update(copy.deepcopy(tables or {}))
    return entry, created


def remove_material(doc: dict[str, Any], model_part_name: str | None = None,
                    properties_id: int | None = None) -> int:
    if model_part_name is None and properties_id is None:
        raise ValueError("give model_part_name and/or properties_id")
    entries = material_entries(doc)
    keep = [e for e in entries
            if not ((model_part_name is None or e.get("model_part_name") == model_part_name)
                    and (properties_id is None or e.get("properties_id") == properties_id))]
    removed = len(entries) - len(keep)
    if not removed:
        raise ValueError("No matching material entry")
    doc["properties"] = keep
    return removed
