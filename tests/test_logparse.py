from __future__ import annotations

from kratos_mcp import logparse

SAMPLE = """\
::[KSM Simulation]:: : STEP:  1
::[KSM Simulation]:: : TIME:  0.1
RESIDUAL CRITERION :: ITERATION:  1
RESIDUAL CRITERION :: [ Obtained ratio = 0.5; Expected ratio = 0.0001 ]
RESIDUAL CRITERION :: ITERATION:  2
RESIDUAL CRITERION :: [ Obtained ratio = 1e-05; Expected ratio = 0.0001 ]
Convergence is achieved after 2 iterations
::[KSM Simulation]:: : STEP:  2
::[KSM Simulation]:: : TIME:  0.2
RESIDUAL CRITERION :: ITERATION:  1
Maximum iterations reached
"""


def test_progress():
    p = logparse.progress(SAMPLE)
    assert p["current_step"] == 2
    assert p["current_time"] == 0.2
    assert p["num_steps_seen"] == 2


def test_progress_detects_errors():
    p = logparse.progress("something\nRuntimeError: Error: element not registered\n")
    assert p["errors_detected"]


def test_convergence():
    c = logparse.convergence(SAMPLE)
    assert c["num_steps"] == 2
    assert c["steps"][0]["converged"] is True
    assert c["steps"][0]["iterations"] == 2
    assert c["steps"][0]["time"] == 0.1
    assert c["steps"][1]["converged"] is False
    assert c["num_not_converged"] == 1
    assert c["max_iterations_in_a_step"] == 2


def test_empty_log():
    assert logparse.progress("")["current_step"] is None
    assert logparse.convergence("")["num_steps"] == 0
