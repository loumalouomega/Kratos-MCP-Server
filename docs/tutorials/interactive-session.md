# Interactive session: load stepping

This tutorial drives a cantilever **step by step** instead of running it to
completion — the "advance a bit, look, decide, continue" workflow that
[interactive sessions](/tools/sessions) add next to batch jobs. The numbers
below come from a real run (Kratos 10.4); the same walkthrough is the
[`interactive_session` notebook](https://github.com/loumalouomega/Kratos-MCP-Server/blob/master/notebooks/interactive_session.ipynb).

## The case

A 5 m × 0.5 m plane-strain steel cantilever (105 nodes, 80 quads), fixed on
`Structure.left`. The tip line load on `Structure.right` is **ramped with
time**: `"modulus": "1.0e6*t"` N/m, downward. `time_step` is `0.125` — exactly
representable in binary — so the eight steps land on t = 0.125, 0.25, … 1.0
with no floating-point drift (`0.1` would accumulate to `0.99999…` and run a
ninth step). The files ship with the server:

```
read kratos://examples/load-stepping-cantilever   # mesh + ProjectParameters + Materials + result
```

Copy them to a working directory (or `create_project` + `add_boundary_condition`
to build the equivalent yourself) and use that directory as `case_dir`.

## 1. Open a session

```
session_start(case_dir="/tmp/lsc", isolate=true)
```

Returns the `session_id` plus `time: 0.0`, `end_time: 1.0`, 105 nodes, 80
elements. `isolate=true` works on a private copy so the example files stay
pristine. The Kratos banner and solver chatter go to the session's
`session.log`, never into the tool results.

Check what will actually be solved, after Kratos applied its defaults:

```
session_get_parameters(session_id, path="/solver_settings/time_stepping")
→ { "path": "...", "parameters": { "time_step": 0.125 } }
```

## 2. Advance two steps and look

```
session_advance(session_id, steps=2)
→ time 0.25, finished false, history: step 1 (t=0.125), step 2 (t=0.25), converged: true
```

```
session_field_summary(session_id, variable="DISPLACEMENT")
→ max |u| = 1.972e-03 m at node 21 (x ≈ 5.0): the free end, as expected
session_inspect_node(session_id, node_id=105, variables=["DISPLACEMENT"])
→ uy = -1.9667e-03 m, fixed: [false, false, false]
session_field_summary(session_id, variable="VON_MISES_STRESS", location="elements")
→ max 1.209e+07 Pa at element 1 (the clamped root)
```

The peak stress sits at the root element next to the support — exactly where a
cantilever should be most stressed — and nothing was written to disk beyond the
VTK files the case already configures.

## 3. Finish the run

```
session_advance(session_id, steps=100)
→ advanced 6, time 1.0, finished true, stop_reason "end_time_reached"
```

Asking for more steps than remain is safe: the loop stops at `end_time`. After
all eight steps, the tip node (id 105):

| step | time | uy(tip) [m] | max von Mises [Pa] |
| ---: | ---: | ---: | ---: |
| 1 | 0.125 | -9.833e-04 | 6.046e+06 |
| 2 | 0.250 | -1.967e-03 | 1.209e+07 |
| 4 | 0.500 | -3.933e-03 | 2.418e+07 |
| 8 | 1.000 | -7.867e-03 | 4.837e+07 |

The response is linear in the ramp (`uy ≈ -7.867e-3 · t`, to ~1e-5 relative),
as it must be for a linear static problem — a useful sanity check you can do
*between* steps rather than after the fact.

## 4. Steer the model

A session lets you change the live state. Pin the tip's vertical displacement
at its current value for the next steps — a stand-in for "what if a prop is
added at step 4?":

```
session_set_nodal_values(session_id, variable="DISPLACEMENT",
                         value=[0.0, -9.833e-04, 0.0], node_ids=[105],
                         fix=[false, true, true])
session_advance(session_id, steps=2)
session_inspect_node(session_id, 105)   # uy stays at -9.833e-04, fixed: [false, true, true]
```

(Open a fresh session first if you want the unmodified result — edits live only
in that session's memory.)

When the typed tools are not enough and the server runs with
`KRATOS_MCP_ALLOW_EXEC=1`, drop to Kratos' Python layer directly:

```
session_exec_python(session_id, "result = model['Structure'].GetNode(75).X")
```

## 5. Close, then compare with a batch run

```
session_close(session_id)      # Finalize() flushes VTK output, kills the worker
```

Run the *same* case as a batch job and compare:

```
run_simulation(case_dir="/tmp/lsc_batch", wait_seconds=60)
results_probe(..., variable="DISPLACEMENT", point=[5.0, 0.5, 0.0])
```

The final tip displacement of the stepped session equals the batch result to
numerical precision: stepping changes *when you can look*, not what Kratos
computes — the worker runs the identical `AnalysisStage` step sequence that
`Run()` does.

## When to use which

- **Session**: you want to inspect or steer between steps, debug *where* a
  response goes wrong, probe a transient, or explore a case interactively.
- **Batch job**: production runs, long runs that must survive a server
  restart, multi-stage cases, MPI, [studies](/tutorials/studies), checkpoints.

A fluid variant — watching a lid-driven cavity approach steady state in chunks
— is in the `interactive_fluid` notebook.
