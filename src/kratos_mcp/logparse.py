"""Extraction of progress and convergence data from Kratos simulation logs.

AnalysisStage prints per step (kratos/python_scripts/analysis_stage.py):

    <SimulationName> STEP:  12
    <SimulationName> TIME:  0.12

Nonlinear strategies print residual/iteration lines that vary by
criterion; we capture the common patterns without depending on any one:

    ... ITERATION:  3
    RESIDUAL CRITERION ... :: [ Obtained ratio = 1.2e-05; Expected ratio = 0.0001; ...]
    ... Convergence is achieved after 4 iterations"""

from __future__ import annotations

import re
from typing import Any

_STEP_RE = re.compile(r"\bSTEP:\s*(\d+)")
_TIME_RE = re.compile(r"\bTIME:\s*([-+0-9.eE]+)")
_ITER_RE = re.compile(r"ITERATION:?\s*(\d+)", re.IGNORECASE)
_RATIO_RE = re.compile(r"ratio\s*=\s*([-+0-9.eE]+)", re.IGNORECASE)
_CONVERGED_RE = re.compile(r"convergence\s+is\s+achieved", re.IGNORECASE)
_NOT_CONVERGED_RE = re.compile(
    r"(not\s+achieve[d]?\s+convergence|max(imum)?\s+iterations?\s+(exceeded|reached))",
    re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(Error|ERROR|Traceback|KRATOS.*EXCEPTION|RuntimeError)\b")


def progress(text: str) -> dict[str, Any]:
    """Latest step/time seen in the log, plus error indicators."""
    steps = _STEP_RE.findall(text)
    times = _TIME_RE.findall(text)
    error_lines = [ln.strip() for ln in text.splitlines() if _ERROR_RE.search(ln)]
    return {
        "current_step": int(steps[-1]) if steps else None,
        "current_time": float(times[-1]) if times else None,
        "num_steps_seen": len(steps),
        "errors_detected": error_lines[:10],
    }


def convergence(text: str) -> dict[str, Any]:
    """Per-step nonlinear iteration counts and residual ratios."""
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in text.splitlines():
        m = _STEP_RE.search(line)
        if m:
            if current is not None:
                steps.append(current)
            current = {"step": int(m.group(1)), "time": None,
                       "iterations": 0, "residual_ratios": [], "converged": None}
            continue
        if current is None:
            continue
        m = _TIME_RE.search(line)
        if m and current["time"] is None:
            current["time"] = float(m.group(1))
            continue
        m = _ITER_RE.search(line)
        if m:
            current["iterations"] = max(current["iterations"], int(m.group(1)))
        m = _RATIO_RE.search(line)
        if m and "expected" not in line.lower().split("ratio")[0][-20:].lower():
            try:
                current["residual_ratios"].append(float(m.group(1)))
            except ValueError:
                pass
        if _CONVERGED_RE.search(line):
            current["converged"] = True
        elif _NOT_CONVERGED_RE.search(line):
            current["converged"] = False

    if current is not None:
        steps.append(current)

    converged_steps = [s for s in steps if s["converged"] is True]
    failed_steps = [s for s in steps if s["converged"] is False]
    return {
        "num_steps": len(steps),
        "num_converged": len(converged_steps),
        "num_not_converged": len(failed_steps),
        "max_iterations_in_a_step": max((s["iterations"] for s in steps), default=0),
        "steps": steps,
    }
