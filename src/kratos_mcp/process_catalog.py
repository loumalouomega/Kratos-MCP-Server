"""Default-settings introspection for Kratos Python processes.

Kratos processes declare their accepted parameters as a ``default_settings``
Kratos ``Parameters`` block validated via ``ValidateAndAssignDefaults`` inside
the process class' ``__init__`` (or, for a few, a ``GetDefaultParameters``
classmethod). There is no runtime registry to query these, so -- exactly like
``source_catalog`` parses C++ registration macros -- we recover them by parsing
the process ``.py`` source with the ``ast`` module. This is pure text/AST work:
it never imports KratosMultiphysics, so it is safe in the server process.

The extraction heuristic is ported from Flowgraph's ``parse-processes.py``:
find the module-level ``Factory`` function, the class it returns, that class'
``__init__``, the argument passed to ``*.ValidateAndAssignDefaults(...)``, and
the triple-quoted ``Parameters(...)`` block assigned to that name. Kratos JSON
tolerates ``//`` comments and trailing commas, which ``json.loads`` does not,
so we strip those first. Anything that does not match returns ``None`` rather
than raising."""

from __future__ import annotations

import ast
import functools
import json
import re
import warnings
from pathlib import Path
from typing import Any

from . import source_catalog

# ------------------------------------------------------------------ JSON prep

_LINE_COMMENT_RE = re.compile(r"//[^\n\r]*")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _loads_kratos_json(text: str) -> dict[str, Any] | None:
    """Parse a Kratos-Parameters JSON string, tolerating // comments and
    trailing commas. Returns None on failure."""
    cleaned = _LINE_COMMENT_RE.sub("", text)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    try:
        value = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------ AST helpers

def _iter_defs(node: ast.AST, kind: type) -> list[ast.AST]:
    return [n for n in ast.iter_child_nodes(node) if isinstance(n, kind)]


def _find_named(node: ast.AST, kind: type, name: str):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, kind) and name in getattr(child, "name", ""):
            return child
    return None


def _parameters_string_from_call(call: ast.Call) -> str | None:
    """Return the raw JSON string from a ``...Parameters("...")`` call."""
    func = call.func
    is_parameters = (
        (isinstance(func, ast.Attribute) and func.attr == "Parameters")
        or (isinstance(func, ast.Name) and func.id == "Parameters")
    )
    if not is_parameters or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _default_settings_from_init(cls: ast.ClassDef) -> str | None:
    """Recover the default-settings JSON string from a class' __init__ by
    following the ValidateAndAssignDefaults argument to its assignment."""
    init = _find_named(cls, ast.FunctionDef, "__init__")
    if init is None:
        return None

    # Name of the local passed to *.ValidateAndAssignDefaults(<name>) /
    # *.RecursivelyValidateAndAssignDefaults(<name>).
    defaults_name: str | None = None
    for call in (n for n in ast.walk(init) if isinstance(n, ast.Call)):
        func = call.func
        if (isinstance(func, ast.Attribute)
                and func.attr in ("ValidateAndAssignDefaults",
                                  "RecursivelyValidateAndAssignDefaults",
                                  "AddMissingParameters")
                and call.args and isinstance(call.args[0], ast.Name)):
            defaults_name = call.args[0].id
            break

    if defaults_name is not None:
        for assign in (n for n in ast.walk(init) if isinstance(n, ast.Assign)):
            targets = [t.id for t in assign.targets if isinstance(t, ast.Name)]
            if defaults_name in targets and isinstance(assign.value, ast.Call):
                raw = _parameters_string_from_call(assign.value)
                if raw is not None:
                    return raw

    # Fallback: any Parameters("""{...}""") assigned to a *default*-looking name.
    for assign in (n for n in ast.walk(init) if isinstance(n, ast.Assign)):
        names = [t.id.lower() for t in assign.targets if isinstance(t, ast.Name)]
        if any("default" in n for n in names) and isinstance(assign.value, ast.Call):
            raw = _parameters_string_from_call(assign.value)
            if raw is not None:
                return raw
    return None


def _default_settings_from_classmethod(cls: ast.ClassDef) -> str | None:
    """Recover defaults from a ``GetDefaultParameters`` method returning a
    ``Parameters(...)`` triple-quoted block (the alternative Kratos convention)."""
    method = _find_named(cls, ast.FunctionDef, "GetDefaultParameters")
    if method is None:
        return None
    for ret in (n for n in ast.walk(method) if isinstance(n, ast.Return)):
        if isinstance(ret.value, ast.Call):
            raw = _parameters_string_from_call(ret.value)
            if raw is not None:
                return raw
    return None


def extract_default_settings(code: str) -> dict[str, Any] | None:
    """Extract a process' default settings dict from its source code, or None.

    Tries, in order: the class returned by the module ``Factory`` function; a
    ``GetDefaultParameters`` classmethod on that class; then every class in the
    module (so processes without a conventional Factory still resolve)."""
    try:
        with warnings.catch_warnings():
            # Real Kratos process files contain regex strings with invalid
            # escapes; ast.parse warns about them. Not our concern here.
            warnings.simplefilter("ignore", SyntaxWarning)
            module = ast.parse(code)
    except SyntaxError:
        return None

    candidate_classes: list[ast.ClassDef] = []

    factory = _find_named(module, ast.FunctionDef, "Factory")
    if factory is not None:
        for ret in (n for n in ast.walk(factory) if isinstance(n, ast.Return)):
            func = getattr(ret.value, "func", None)
            class_name = getattr(func, "id", None)
            if class_name:
                cls = _find_named(module, ast.ClassDef, class_name)
                if cls is not None:
                    candidate_classes.append(cls)

    # Also consider every top-level class as a fallback source of defaults.
    for cls in _iter_defs(module, ast.ClassDef):
        if cls not in candidate_classes:
            candidate_classes.append(cls)

    for cls in candidate_classes:
        for extractor in (_default_settings_from_init,
                          _default_settings_from_classmethod):
            raw = extractor(cls)
            if raw is not None:
                parsed = _loads_kratos_json(raw)
                if parsed is not None:
                    return parsed
    return None


# ------------------------------------------------------------------ typing/split

def param_type(value: Any) -> str:
    """Map a default value to a coarse widget/schema type."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "json"
    return "null"


def param_types(defaults: dict[str, Any]) -> dict[str, str]:
    return {key: param_type(value) for key, value in defaults.items()}


def _split_params(defaults: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    """Split defaults into (help text, model-part input keys, other params),
    mirroring Flowgraph's get_node_params heuristic."""
    remaining = dict(defaults)
    help_text = remaining.pop("help", "")
    if not isinstance(help_text, str):
        help_text = ""
    inputs: list[str] = []
    others: dict[str, Any] = {}
    for key, value in remaining.items():
        if "computing_model_part_name" in key:
            continue  # solver-managed, not user-facing
        if "model_part" in key:
            inputs.append(key)
        else:
            others[key] = value
    return help_text, inputs, others


# ------------------------------------------------------------------ public API

@functools.lru_cache(maxsize=1)
def _process_index() -> dict[str, Path]:
    """Map process module stem -> source path (first occurrence wins)."""
    index: dict[str, Path] = {}
    for _app, path in source_catalog.python_process_files():
        index.setdefault(path.stem, path)
    return index


@functools.lru_cache(maxsize=256)
def get_process_defaults(python_module: str) -> dict[str, Any] | None:
    """Return the parsed default settings for a process python_module, or None
    when the source is unavailable or cannot be parsed.

    Result shape: {python_module, default_settings, param_types,
    input_model_parts, output_params, help}."""
    path = _process_index().get(python_module)
    if path is None:
        return None
    try:
        code = path.read_text(errors="replace")
    except OSError:
        return None
    defaults = extract_default_settings(code)
    if defaults is None:
        return None
    help_text, inputs, others = _split_params(defaults)
    return {
        "python_module": python_module,
        "default_settings": defaults,
        "param_types": param_types(defaults),
        "input_model_parts": inputs,
        "output_params": others,
        "help": help_text,
    }
