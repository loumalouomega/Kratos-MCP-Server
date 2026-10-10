"""Adaptive remeshing (MeshingApplication / MMG) support — pure Python, no Kratos import.

Two entry points share this module:

* ``mesh_remesh`` — a one-shot tool: an ``.mdpa`` goes in, MMG remeshes it in the
  ``remesh_mdpa`` worker op, a new ``.mdpa`` comes out. This module validates the
  request, turns it into ``mmg_process`` settings plus a list of nodal
  expressions, and builds the before/after report from the two ``.mdpa`` files.
* ``add_remeshing_process`` — inserts an ``mmg_process`` block into a
  ProjectParameters.json so a run remeshes while it executes. ``is_remesh_process``
  / ``find_processes`` are the detection point used by the rejections elsewhere
  (remeshing changes node/element ids, so studies, checkpoints, MPI and sessions
  refuse such cases).

Metric inputs are *expressions* in ``x``, ``y``, ``z`` evaluated per node (a region
becomes a signed-distance expression). They go through ``compile_expression``, an
AST whitelist, because the same string is evaluated again inside the worker.
"""

from __future__ import annotations

import ast
import math
from typing import Any, Callable

from . import mdpa as mdpa_mod

STRATEGIES = ("levelset", "hessian", "uniform")
REGION_TYPES = ("sphere", "box")
REQUIRED_APPLICATIONS = ("MeshingApplication",)
MMG_MODULE = "mmg_process"
MMG_KRATOS_MODULE = "KratosMultiphysics.MeshingApplication"

_FUNCTIONS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "exp": math.exp, "log": math.log, "tanh": math.tanh, "atan": math.atan,
    "abs": abs, "min": min, "max": max,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_VARIABLES = ("x", "y", "z")
_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_MAX_EXPRESSION_LENGTH = 500
_MAX_POWER = 16


class MeshingUnavailable(RuntimeError):
    """The configured Kratos has no MMG-enabled MeshingApplication."""

    def __init__(self, support: dict[str, Any]):
        super().__init__("MeshingApplication/MMG not available")
        self.support = support


# ------------------------------------------------------------------ expressions

def compile_expression(expression: str) -> Callable[[float, float, float], float]:
    """Compile ``expression`` into ``f(x, y, z)``. Only arithmetic, the names
    x/y/z/pi/e and the functions in ``_FUNCTIONS`` are allowed; anything else
    raises ValueError."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError(f"expression longer than {_MAX_EXPRESSION_LENGTH} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.UnaryOp, ast.USub, ast.UAdd)):
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BIN_OPS):
                raise ValueError("unsupported operator in expression")
            if isinstance(node.op, ast.Pow) and not _is_small_constant(node.right):
                raise ValueError(f"exponent must be a numeric constant with |n| <= {_MAX_POWER}")
        elif isinstance(node, _BIN_OPS):
            continue
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("only numeric constants are allowed")
        elif isinstance(node, ast.Name):
            if node.id not in _VARIABLES and node.id not in _CONSTANTS and node.id not in _FUNCTIONS:
                raise ValueError(f"unknown name '{node.id}' (use x, y, z, pi, e or "
                                 f"{', '.join(sorted(_FUNCTIONS))})")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS or node.keywords:
                raise ValueError(f"only these functions are allowed: {', '.join(sorted(_FUNCTIONS))}")
        else:
            raise ValueError(f"unsupported syntax in expression: {type(node).__name__}")

    code = compile(tree, "<expression>", "eval")
    namespace = {"__builtins__": {}, **_FUNCTIONS, **_CONSTANTS}

    def evaluate(x: float, y: float = 0.0, z: float = 0.0) -> float:
        return float(eval(code, namespace, {"x": x, "y": y, "z": z}))  # noqa: S307 - whitelisted AST

    return evaluate


def _is_small_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool) and abs(node.value) <= _MAX_POWER)


def region_expression(region: dict[str, Any], domain_size: int) -> str:
    """Signed-distance expression of a region: negative inside, positive outside.

    ``sphere`` (circle in 2D): ``center`` and ``radius``. ``box``: ``min`` and
    ``max`` corners. ``mode`` is ``inside`` (default; the whole region gets the
    minimal size, the distance is clamped at 0) or ``surface`` (only a layer
    around the region's boundary is refined)."""
    kind = region.get("type")
    if kind not in REGION_TYPES:
        raise ValueError(f"region.type must be one of {list(REGION_TYPES)}")
    mode = region.get("mode", "inside")
    if mode not in ("inside", "surface"):
        raise ValueError("region.mode must be 'inside' or 'surface'")
    axes = _VARIABLES[:domain_size]

    def vector(key: str) -> list[float]:
        value = region.get(key)
        if (not isinstance(value, (list, tuple)) or len(value) not in (2, 3)
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            raise ValueError(f"region.{key} must be a list of 2 or 3 numbers")
        padded = list(value) + [0.0] * (3 - len(value))
        return [float(v) for v in padded[:domain_size]]

    if kind == "sphere":
        centre = vector("center")
        radius = region.get("radius")
        if not isinstance(radius, (int, float)) or isinstance(radius, bool) or radius <= 0:
            raise ValueError("region.radius must be a positive number")
        squares = " + ".join(f"({axis} - {c!r})**2" for axis, c in zip(axes, centre))
        signed = f"sqrt({squares}) - {float(radius)!r}"
    else:
        low, high = vector("min"), vector("max")
        if any(lo >= hi for lo, hi in zip(low, high)):
            raise ValueError("region.min must be smaller than region.max on every axis")
        offsets = [f"max({lo!r} - {axis}, {axis} - {hi!r})" for axis, lo, hi in zip(axes, low, high)]
        outside = " + ".join(f"max({o}, 0.0)**2" for o in offsets)
        deepest = offsets[0] if len(offsets) == 1 else f"max({', '.join(offsets)})"
        signed = f"sqrt({outside}) + min({deepest}, 0.0)"
    return f"max({signed}, 0.0)" if mode == "inside" else signed


# ------------------------------------------------------------------- statistics

def element_sizes(mesh: mdpa_mod.Mdpa) -> list[float]:
    """Characteristic size of every element: mean distance between its node pairs."""
    sizes: list[float] = []
    for by_id in mesh.elements.values():
        for _, conn in by_id.values():
            points = [mesh.nodes[n] for n in conn if n in mesh.nodes]
            pairs = [math.dist(a, b) for i, a in enumerate(points) for b in points[i + 1:]]
            if pairs:
                sizes.append(sum(pairs) / len(pairs))
    return sizes


def domain_size(mesh: mdpa_mod.Mdpa) -> int:
    box = mesh.bounding_box()
    if box is None:
        return 2
    return 3 if box["max"][2] - box["min"][2] > 1e-12 else 2


def summarize_mesh(mesh: mdpa_mod.Mdpa) -> dict[str, Any]:
    info = mesh.inspect()
    sizes = element_sizes(mesh)
    info["element_size"] = ({"min": min(sizes), "mean": sum(sizes) / len(sizes), "max": max(sizes)}
                            if sizes else None)
    return info


def _find_part(mesh: mdpa_mod.Mdpa, path: str) -> mdpa_mod.SubModelPart | None:
    part: mdpa_mod.SubModelPart | None = None
    parts = mesh.sub_model_parts
    for segment in path.split("."):
        part = parts.get(segment)
        if part is None:
            return None
        parts = part.children
    return part


def part_signature(mesh: mdpa_mod.Mdpa, path: str) -> dict[str, Any] | None:
    """Counts and node bounding box of a sub model part (None when it is absent)."""
    part = _find_part(mesh, path)
    if part is None:
        return None
    coords = [mesh.nodes[n] for n in part.nodes if n in mesh.nodes]
    box = None
    if coords:
        xs, ys, zs = zip(*coords)
        box = {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}
    return {"nodes": len(part.nodes), "elements": len(part.elements),
            "conditions": len(part.conditions), "bounding_box": box}


def boundary_parts(mesh: mdpa_mod.Mdpa) -> list[str]:
    """Dotted paths of every sub model part that carries conditions."""
    found: list[str] = []

    def walk(part: mdpa_mod.SubModelPart, prefix: str) -> None:
        path = f"{prefix}{part.name}"
        if part.conditions:
            found.append(path)
        for child in part.children.values():
            walk(child, path + ".")

    for part in mesh.sub_model_parts.values():
        walk(part, "")
    return found


def preservation_report(before: mdpa_mod.Mdpa, after: mdpa_mod.Mdpa, names: list[str],
                        tolerance: float = 1e-9) -> dict[str, Any]:
    """Per requested sub model part: still present, still populated, geometry unchanged
    (node bounding box within ``tolerance``)."""
    report: dict[str, Any] = {}
    for name in names:
        old, new = part_signature(before, name), part_signature(after, name)
        if old is None:
            report[name] = {"ok": False, "reason": "not present in the input mesh"}
            continue
        if new is None:
            report[name] = {"ok": False, "reason": "missing after remeshing", "before": old}
            continue
        problems = []
        if old["conditions"] and not new["conditions"]:
            problems.append("conditions lost")
        if old["nodes"] and not new["nodes"]:
            problems.append("nodes lost")
        if old["bounding_box"] and new["bounding_box"]:
            drift = max(abs(a - b) for key in ("min", "max")
                        for a, b in zip(old["bounding_box"][key], new["bounding_box"][key]))
            if drift > tolerance:
                problems.append(f"bounding box moved by {drift:.3g}")
        report[name] = {"ok": not problems, "before": old, "after": new,
                        **({"problems": problems} if problems else {})}
    return report


# ----------------------------------------------------------------------- request

def build_request(
    mesh: mdpa_mod.Mdpa,
    strategy: str = "levelset",
    region: dict[str, Any] | None = None,
    distance_expression: str | None = None,
    field_variable: str | None = None,
    field_expression: str | None = None,
    min_size: float | None = None,
    max_size: float | None = None,
    transition_distance: float | None = None,
    freeze_sub_model_parts: list[str] | None = None,
    check_fields: list[dict[str, str]] | None = None,
    interpolation_error: float | None = None,
) -> dict[str, Any]:
    """Validate the arguments of ``mesh_remesh`` against ``mesh`` and return the
    worker request: ``{domain_size, settings, assign, check}`` where ``settings`` is
    the ``mmg_process`` ``Parameters`` block and ``assign``/``check`` are lists of
    ``{"variable", "expression"}`` evaluated per node. Raises ValueError."""
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {list(STRATEGIES)}")
    if not mesh.nodes or not mesh.elements:
        raise ValueError("the input mesh has no nodes or elements")
    dim = domain_size(mesh)
    sizes = element_sizes(mesh)
    mean_size = sum(sizes) / len(sizes)

    for label, value in (("min_size", min_size), ("max_size", max_size),
                         ("transition_distance", transition_distance),
                         ("interpolation_error", interpolation_error)):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{label} must be a positive number")
    if strategy == "uniform":
        if min_size is not None and max_size is not None and min_size != max_size:
            raise ValueError("uniform remeshing takes a single target size: set min_size or "
                             "max_size (or both equal)")
        target = min_size if min_size is not None else max_size
        if target is None:
            raise ValueError("uniform remeshing requires min_size (the target element size)")
        lo = hi = float(target)
    else:
        hi = float(max_size) if max_size is not None else mean_size
        lo = float(min_size) if min_size is not None else hi / 4.0
        if lo > hi:
            raise ValueError("min_size must not exceed max_size")

    settings: dict[str, Any] = {
        "model_part_name": "Main",
        "automatic_remesh": False,
        "minimal_size": lo, "maximal_size": hi,
        "force_min": True, "force_max": True,
        "anisotropy_remeshing": False,
        "enforce_current": False,
        "initial_remeshing": True,
        "echo_level": 0,
    }
    assign: list[dict[str, str]] = []

    if strategy == "levelset":
        if (region is None) == (distance_expression is None):
            raise ValueError("levelset remeshing requires exactly one of region or "
                             "distance_expression")
        expression = (region_expression(region, dim) if region is not None
                      else str(distance_expression))
        compile_expression(expression)
        assign.append({"variable": "DISTANCE", "expression": expression})
        settings["strategy"] = "levelset"
        settings["level_set_strategy_parameters"] = {
            "scalar_variable": "DISTANCE", "gradient_variable": "DISTANCE_GRADIENT"}
        settings["sizing_parameters"] = {
            "reference_variable_name": "DISTANCE",
            "boundary_layer_max_distance": float(transition_distance or 3.0 * hi),
            "interpolation": "Linear"}
    elif strategy == "hessian":
        if not field_variable or not field_expression:
            raise ValueError("hessian remeshing requires field_variable and field_expression")
        compile_expression(field_expression)
        assign.append({"variable": field_variable, "expression": field_expression})
        settings["strategy"] = "hessian"
        settings["hessian_strategy_parameters"] = {
            "metric_variable": [field_variable],
            "non_historical_metric_variable": [False],
            "estimate_interpolation_error": False,
            "interpolation_error": float(interpolation_error or 0.04),
        }
    else:
        # A constant level set with a forced min == max size is a uniform metric.
        assign.append({"variable": "DISTANCE", "expression": "1.0"})
        settings["strategy"] = "levelset"
        settings["level_set_strategy_parameters"] = {
            "scalar_variable": "DISTANCE", "gradient_variable": "DISTANCE_GRADIENT"}
        settings["sizing_parameters"] = {
            "reference_variable_name": "DISTANCE",
            "boundary_layer_max_distance": 1.0, "interpolation": "Linear"}

    freeze = list(freeze_sub_model_parts or [])
    missing = [name for name in freeze if _find_part(mesh, name) is None]
    if missing:
        raise ValueError(f"freeze_sub_model_parts not found in the mesh: {missing}; available: "
                         f"{sorted(mesh.sub_model_parts)}")
    if freeze:
        settings["fix_contour_model_parts"] = freeze

    check: list[dict[str, str]] = []
    for item in check_fields or []:
        if (not isinstance(item, dict) or not item.get("variable")
                or not isinstance(item.get("expression"), str)):
            raise ValueError("each check_fields entry needs 'variable' and 'expression'")
        compile_expression(item["expression"])
        check.append({"variable": str(item["variable"]), "expression": item["expression"]})
    return {"domain_size": dim, "settings": settings, "assign": assign, "check": check}


def report(before: mdpa_mod.Mdpa, after: mdpa_mod.Mdpa, preserve: list[str]) -> dict[str, Any]:
    """Before/after summary of a remeshing run plus the boundary preservation check."""
    old, new = summarize_mesh(before), summarize_mesh(after)
    change = {
        "nodes": new["num_nodes"] - old["num_nodes"],
        "elements": new["num_elements"] - old["num_elements"],
        "conditions": new["num_conditions"] - old["num_conditions"],
        "element_count_ratio": (new["num_elements"] / old["num_elements"]
                                if old["num_elements"] else None),
    }
    return {"before": old, "after": new, "change": change,
            "preserved": preservation_report(before, after, preserve),
            "issues": after.validate()}


# ----------------------------------------------------- in-simulation process block

def process_block(
    model_part: str,
    strategy: str = "hessian",
    variable: str | None = None,
    min_size: float | None = None,
    max_size: float | None = None,
    interval: list | None = None,
    step_frequency: int = 0,
    initial_remeshing: bool = True,
    freeze_sub_model_parts: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``mmg_process`` block for ``processes.list_other_processes``. ``strategy`` is
    ``hessian`` (metric from ``variable``, a nodal scalar variable) or ``levelset``
    (``variable`` must hold a signed distance and ``<variable>_GRADIENT`` must exist
    in the model part).

    ``initial_remeshing`` (default) remeshes in ``ExecuteInitialize``, i.e. before
    the solver builds its strategy: safe, but the field driving the metric must
    already be on the nodes then (mdpa ``NodalData``). Remeshing inside the solution
    loop (``initial_remeshing`` false, ``step_frequency`` > 0) crashed Kratos 10.4
    thermal runs (SIGSEGV on the stale solver strategy) and is warned about by
    ``validate``."""
    if strategy not in ("hessian", "levelset"):
        raise ValueError("in-simulation remeshing supports strategy 'hessian' or 'levelset'")
    if not variable:
        raise ValueError(f"strategy '{strategy}' requires 'variable' (the nodal field driving "
                         "the metric)")
    if not model_part:
        raise ValueError("model_part is required")
    for label, value in (("min_size", min_size), ("max_size", max_size)):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or value <= 0):
            raise ValueError(f"{label} must be a positive number")
    if min_size is not None and max_size is not None and min_size > max_size:
        raise ValueError("min_size must not exceed max_size")
    if not isinstance(step_frequency, int) or step_frequency < 0:
        raise ValueError("step_frequency must be a non-negative integer")

    params: dict[str, Any] = {
        "model_part_name": model_part,
        "strategy": strategy,
        "interval": interval if interval is not None else [0.0, "End"],
        "remesh_control_type": "step",
        "initial_step": 1,
        "step_frequency": step_frequency,
        "initial_remeshing": initial_remeshing,
        "automatic_remesh": min_size is None and max_size is None,
        "echo_level": 1,
    }
    if min_size is not None:
        params.update(minimal_size=float(min_size), force_min=True)
    if max_size is not None:
        params.update(maximal_size=float(max_size), force_max=True)
    if strategy == "hessian":
        params["hessian_strategy_parameters"] = {"metric_variable": [variable]}
    else:
        params["level_set_strategy_parameters"] = {
            "scalar_variable": variable, "gradient_variable": f"{variable}_GRADIENT"}
        params["sizing_parameters"] = {"reference_variable_name": variable}
    if freeze_sub_model_parts:
        params["fix_contour_model_parts"] = list(freeze_sub_model_parts)
    if extra:
        params.update(extra)
    return {"python_module": MMG_MODULE, "kratos_module": MMG_KRATOS_MODULE,
            "process_name": "MmgProcess", "Parameters": params}


# ---------------------------------------------------------------------- detection

def is_remesh_process(block: Any) -> bool:
    return (isinstance(block, dict)
            and str(block.get("python_module", "")).lower() == MMG_MODULE)


def find_processes(params: Any) -> list[dict[str, Any]]:
    """Every ``mmg_process`` block anywhere in a ProjectParameters structure."""
    found: list[dict[str, Any]] = []
    if isinstance(params, dict):
        if is_remesh_process(params):
            found.append(params)
        else:
            for value in params.values():
                found += find_processes(value)
    elif isinstance(params, list):
        for item in params:
            found += find_processes(item)
    return found


def has_remeshing(params: Any) -> bool:
    return bool(find_processes(params))


def summarize(params: Any) -> list[dict[str, Any]]:
    """Short description of each remeshing process for ``explain_project_parameters``."""
    out = []
    for block in find_processes(params):
        p = block.get("Parameters", {}) if isinstance(block.get("Parameters"), dict) else {}
        out.append({
            "model_part": p.get("model_part_name"),
            "strategy": p.get("strategy", "levelset"),
            "remesh_control_type": p.get("remesh_control_type", "step"),
            "step_frequency": p.get("step_frequency", 0),
            "initial_remeshing": p.get("initial_remeshing", False),
            "minimal_size": p.get("minimal_size"),
            "maximal_size": p.get("maximal_size"),
        })
    return out


def validate(params: dict[str, Any], root_model_part: str | None,
             meshing_available: bool | None) -> tuple[list[str], list[str]]:
    """Static checks of the mmg_process blocks of a case -> (issues, warnings)."""
    issues: list[str] = []
    warnings: list[str] = []
    for block in find_processes(params):
        p = block.get("Parameters")
        if not isinstance(p, dict):
            issues.append("mmg_process block has no Parameters object")
            continue
        strategy = p.get("strategy", "levelset")
        if strategy not in ("levelset", "hessian", "spr", "superconvergent_patch_recovery",
                            "optimization"):
            issues.append(f"mmg_process strategy '{strategy}' is not supported by MmgProcess")
        name = p.get("model_part_name")
        if not isinstance(name, str) or not name or name == "PLEASE_SPECIFY_MODEL_PART_NAME":
            issues.append("mmg_process requires model_part_name")
        elif root_model_part and name != root_model_part:
            warnings.append(f"mmg_process remeshes '{name}' but the solver's root model part is "
                            f"'{root_model_part}'; MMG needs the root model part of the mesh")
        if not p.get("initial_remeshing", False) or p.get("remesh_control_type", "step") != "step" \
                or p.get("step_frequency", 0):
            warnings.append(
                "mmg_process remeshes inside the solution loop: standard Kratos solvers keep "
                "their strategy bound to the old mesh and abort (SIGSEGV) after the first "
                "remesh; use initial_remeshing=true (remesh before the solver initializes) "
                "unless the solver is built for it")
        lo, hi = p.get("minimal_size"), p.get("maximal_size")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
            issues.append("mmg_process minimal_size exceeds maximal_size")
        if strategy == "hessian":
            metric = (p.get("hessian_strategy_parameters") or {}).get("metric_variable")
            if not metric:
                warnings.append("mmg_process hessian strategy has no metric_variable "
                                "(defaults to DISTANCE)")
        if meshing_available is False:
            issues.append("mmg_process needs KratosMeshingApplication built with MMG; the "
                          "configured Kratos does not provide it")
    return issues, warnings
