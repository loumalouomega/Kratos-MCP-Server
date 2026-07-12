"""Catalogs of Kratos elements/conditions/constitutive laws/processes.

Kratos has no runtime registry for these (registration macros only fill
internal maps queried by exact name), so we parse the registration
macros straight from the C++ sources:

    KRATOS_REGISTER_ELEMENT("Name", mVariable)
    KRATOS_REGISTER_CONDITION("Name", mVariable)
    KRATOS_REGISTER_CONSTITUTIVE_LAW("Name", mVariable)

Python processes are discovered by scanning python_scripts/*_process.py.
Results are cached in-process; parsing the whole tree takes < 1 s.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

from . import kratos_env

_MACROS = {
    "elements": ("KRATOS_REGISTER_ELEMENT", "KRATOS_REGISTER_ELEMENT_WITH_GEOMETRY"),
    "conditions": ("KRATOS_REGISTER_CONDITION", "KRATOS_REGISTER_CONDITION_WITH_GEOMETRY"),
    "constitutive_laws": ("KRATOS_REGISTER_CONSTITUTIVE_LAW",),
}

_NAME_RE = re.compile(r'\(\s*"([^"]+)"')


def _kratos_source() -> Path | None:
    env = kratos_env.resolve()
    if env.source is not None and env.source.is_dir():
        return env.source
    return None


def _registration_files(source: Path) -> list[tuple[str, Path]]:
    """(application_name, file) pairs likely to contain registration macros."""
    files: list[tuple[str, Path]] = []
    core = source / "kratos" / "sources"
    if core.is_dir():
        files += [("Core", f) for f in core.glob("kratos_application.cpp")]
    apps_dir = source / "applications"
    if apps_dir.is_dir():
        for app in sorted(apps_dir.iterdir()):
            if not app.is_dir():
                continue
            for f in app.glob("*_application.cpp"):
                files.append((app.name, f))
    return files


@functools.lru_cache(maxsize=1)
def build_catalog() -> dict[str, list[dict[str, str]]]:
    """Parse all registration macros once. Returns
    {"elements": [{"name": ..., "application": ...}, ...], ...}"""
    source = _kratos_source()
    catalog: dict[str, list[dict[str, str]]] = {k: [] for k in _MACROS}
    if source is None:
        return catalog
    for app_name, path in _registration_files(source):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for category, macros in _MACROS.items():
            seen: set[str] = set()
            for macro in macros:
                for match in re.finditer(re.escape(macro) + r"\s*\(\s*\"([^\"]+)\"", text):
                    name = match.group(1)
                    if name not in seen:
                        seen.add(name)
                        catalog[category].append({"name": name, "application": app_name})
    return catalog


def list_entities(
    category: str,
    application: str | None = None,
    name_filter: str | None = None,
    compiled_apps: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List registered entities of a category with optional filters.

    compiled_apps, when given, adds a `compiled` flag telling whether the
    owning application exists in the current build (Core is always compiled).
    """
    entries = build_catalog().get(category, [])
    out: list[dict[str, Any]] = []
    for e in entries:
        if application and e["application"].lower() != application.lower():
            continue
        if name_filter and name_filter.lower() not in e["name"].lower():
            continue
        item: dict[str, Any] = dict(e)
        if compiled_apps is not None:
            item["compiled"] = e["application"] == "Core" or e["application"] in compiled_apps
        out.append(item)
    return sorted(out, key=lambda x: (x["application"], x["name"]))


def list_source_applications() -> list[str]:
    source = _kratos_source()
    if source is None or not (source / "applications").is_dir():
        return []
    return sorted(
        d.name for d in (source / "applications").iterdir()
        if d.is_dir() and (d / "CMakeLists.txt").is_file()
    )


@functools.lru_cache(maxsize=1)
def list_python_processes() -> list[dict[str, str]]:
    """Process modules discoverable under python_scripts/ (core + apps)."""
    source = _kratos_source()
    if source is None:
        return []
    results: list[dict[str, str]] = []
    roots: list[tuple[str, Path]] = [("Core", source / "kratos" / "python_scripts")]
    apps_dir = source / "applications"
    if apps_dir.is_dir():
        roots += [(d.name, d / "python_scripts") for d in sorted(apps_dir.iterdir()) if d.is_dir()]
    for app_name, scripts in roots:
        if not scripts.is_dir():
            continue
        for f in sorted(scripts.glob("*_process.py")):
            results.append({"module": f.stem, "application": app_name})
    return results


def list_solver_modules(application: str) -> list[str]:
    """Solver module names under an application's python_scripts."""
    source = _kratos_source()
    if source is None:
        return []
    scripts = source / "applications" / application / "python_scripts"
    if not scripts.is_dir():
        return []
    return sorted(f.stem for f in scripts.glob("*_solver.py"))
