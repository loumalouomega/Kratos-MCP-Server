# Tutorial: coupled solvers (CoSimulation)

Some problems need several solvers advancing together and trading data every
time step — fluid and structure, two thermal domains, or here the simplest
possible stand-in: two oscillators joined by a spring. Kratos'
CoSimulationApplication drives these; this server scaffolds, validates and runs
them. Every number below comes from a real run against Kratos 10.4 with
`KratosCoSimulationApplication` installed.

The `kratos://examples/coupled-sdof` resource is this exact case. A second example,
`kratos://examples/coupled-bar`, couples two real Kratos solvers on non-matching
meshes (last section).

## The problem

`sdof_1` (2 kg, 800 N/m) hangs from a fixed wall; `sdof_2` (1 kg, 400 N/m)
hangs from `sdof_1`. The SDoF solvers are Python-only — no mesh, no element
types — so the only moving parts are the coupling itself. Two quantities cross
the interface each step:

| Data | From → to | Meaning |
| --- | --- | --- |
| `disp` → `root_disp` | `sdof_1` → `sdof_2` | the lower spring's root moves with the upper mass |
| `reaction` → `force` | `sdof_2` → `sdof_1` | the lower spring's reaction loads the upper mass |

## 1. Scaffold the coupling

`create_cosim_project` takes the solvers, the interface data they declare, the
couplings between them, and the coupled-solver settings:

```json
create_cosim_project({ "directory": "/tmp/cs", "coupled_solver": "gauss_seidel_strong",
  "end_time": 0.5, "num_coupling_iterations": 30,
  "solvers": [
    { "name": "sdof_1", "type": "sdof",
      "parameters": { "system_parameters": { "mass": 2.0, "stiffness": 800.0 },
                      "time_integration_parameters": { "time_step": 0.01 } },
      "data": { "disp": { "variable_name": "SCALAR_DISPLACEMENT" },
                "force": { "variable_name": "SCALAR_FORCE" } } },
    { "name": "sdof_2", "type": "sdof",
      "parameters": { "system_parameters": { "mass": 1.0, "stiffness": 400.0 },
                      "time_integration_parameters": { "time_step": 0.01 },
                      "boundary_conditions": { "load_impulse": 10.0 } },
      "data": { "root_disp": { "variable_name": "SCALAR_ROOT_POINT_DISPLACEMENT" },
                "reaction": { "variable_name": "SCALAR_REACTION" } } } ],
  "couplings": [
    { "from_solver": "sdof_1", "from_data": "disp",     "to_solver": "sdof_2", "to_data": "root_disp" },
    { "from_solver": "sdof_2", "from_data": "reaction", "to_solver": "sdof_1", "to_data": "force" } ],
  "convergence_criteria": [ { "type": "relative_norm_initial_residual",
      "solver": "sdof_1", "data_name": "force",
      "abs_tolerance": 1e-10, "rel_tolerance": 1e-8 } ],
  "convergence_accelerators": [ { "type": "aitken", "solver": "sdof_1", "data_name": "force" } ] })
→ { "created": [".../ProjectParameters.json", ".../sdof_1.json", ".../sdof_2.json"],
    "coupling_order": ["sdof_1", "sdof_2"],
    "validation": { "valid": true, "issues": [], "warnings": [] } }
```

Two details worth knowing:

- **Where each exchange lands.** `sdof_1` solves first, so its displacement is
  written as an *output* of its turn (delivered to `sdof_2` right after it
  solves); `sdof_2`'s reaction comes from *later* in the sequence, so it is an
  *input* of `sdof_1` (delivered right before `sdof_1` solves, carrying the
  previous iteration's value). The strong coupling iterates until the force
  residual converges.
- **`location: model_part`.** The SDoF wrappers keep their scalars on the
  model part itself; the `sdof` shorthand fills that in (and the `Sdof` model
  part name) for you.

The top-level file is not a single `AnalysisStage` case: its `solver_settings`
is `coupled_solvers.gauss_seidel_strong` with a `coupling_sequence` and a
`solvers` dict, and each solver points at its own parameter file with
`solver_wrapper_settings.input_file`.

## 2. Catch mistakes before launch

Kratos only discovers a misspelled data name or an undefined transfer operator
when the coupled solver initialises. `validate_case` checks the wiring up
front. Here `sdof_2` is told to receive `root_displacement` (it declares
`root_disp`) and a second transfer asks for an operator `mapper` that was never
defined:

```json
validate_case({ "case_dir": "/tmp/cs" })
→ { "valid": false, "issues": [
  "coupling_sequence[sdof_1].output_data_list[0]: to_solver_data 'root_displacement' is not declared in solver 'sdof_2' data",
  "coupling_sequence[sdof_2].input_data_list[0]: data_transfer_operator 'mapper' is not defined in data_transfer_operators (defined: ['copy'])" ] }
```

The same pass flags unknown solvers in the sequence, solvers missing from it,
undefined before/after operations, strong coupling without convergence
criteria, criteria or accelerators that point at nonexistent data, SDoF
variables the wrapper does not accept, and missing or misspelled sub-solver
input files. For Kratos-wrapped solvers (`solver_wrappers.kratos.*`) each
sub-solver's own parameters file also goes through the normal single-stage
validation (mesh, materials, and the deep Kratos-side check).

## 3. Run it

```json
run_simulation({ "case_dir": "/tmp/cs", "wait_seconds": 60 })
→ { "state": "succeeded", "returncode": 0, "elapsed_seconds": 0.5, ... }
```

`run_simulation` recognises the coupled solver and launches
`CoSimulationAnalysis(cosim_settings)` instead of an `AnalysisStage`. The job
log looks like:

```
time=0.43 | step=43:
GaussSeidelStrongCoupledSolver: Coupling iteration: 1 / 30
GaussSeidelStrongCoupledSolver: ### CONVERGENCE WAS ACHIEVED ###
```

`results_convergence` turns that into per-step coupling-iteration counts: all
50 steps converged, 125 coupling iterations in total, at most 4 in a step (the
pattern alternates 1, 4, 1, 4, …). Kratos also prints a harmless
`AitkenConvergenceAccelerator ... reaches lower bound` warning on some steps.

## 4. Check the data exchange

Each solver writes its own result file (`sdof_1_results.dat`,
`sdof_2_results.dat`; 51 rows: the initial state plus 50 steps). The root-point
displacement column of `sdof_2` is the value that crossed the interface, so it
must equal `sdof_1`'s displacement column — and it does, exactly, with a
**one-step lag**:

    max |sdof_2.root_point[k] - sdof_1.displacement[k-1]| = 0.0
    t = 0.5 s:  u(sdof_1) = -6.165e-04 m,  u(sdof_2) = 2.285e-03 m
    max |u|:    sdof_1 1.779e-03 m,        sdof_2 2.605e-03 m

The lag is how the SDoF wrapper reports `SCALAR_DISPLACEMENT` (the last accepted
displacement, not the one being solved), not a bug in the exchange. It means
this example demonstrates the data flow, not monolithic accuracy: do not
compare it against a single two-DoF solve. With real Kratos solvers the same
scaffold applies, with the interface `model_part_name`s pointing at
sub-model-parts of each solver's own mesh — see the next section.

## 5. Two Kratos solvers on non-matching meshes

`kratos://examples/coupled-bar` splits an elastic bar into two
`solver_wrappers.kratos.structural_mechanics_wrapper` blocks that meet at x = 1 m
with different node counts (5 on `structure_a`'s side, 4 on `structure_b`'s).
`structure_a` (E = 3e9 Pa, fixed at x = 0) is the Neumann block; `structure_b`
(E = 1e9 Pa, traction 1e6 N/m at x = 2) is the Dirichlet block. The exchange goes
through `kratos_mapping` (MappingApplication, `nearest_element`):

| Data | From → to | Operator options |
| --- | --- | --- |
| `interface_displacement` | `structure_a` → `structure_b` | — |
| `interface_reaction` (`REACTION`) → `interface_load` (`POINT_LOAD`) | `structure_b` → `structure_a` | `swap_sign`, `use_transpose` |

`use_transpose` is what makes the load transfer conservative: the nodal reactions
of B's interface are redistributed onto A's nodes so their sum is preserved.
Poisson's ratio is 0, so the solution is exactly uniaxial and the answer is known:
interface displacement σL/E_A = 3.333e-4 m, tip displacement 1.3333e-3 m.

`validate_case` catches the mistakes that make a mesh-based coupling fail inside
Kratos' `Initialize`: a data `model_part_name` whose root is not the sub-solver's
model part or whose sub-model-part is not in its mesh, a missing `"dimension"` on a
vector variable, a `kratos_mapping` without `mapper_type`, and a geometry-based
mapper (`nearest_element`) whose origin interface has no conditions — which is why
both interface sub-model-parts of the example carry geometry-only `LineCondition2D2N`.

```
coupling iterations:  17 (rel. tolerance 1e-6), CONVERGENCE WAS ACHIEVED
sum of A's POINT_LOAD = 2.000000e5 N = -sum of B's REACTION   (force conserved)
interface ux:         3.338e-4 m on A, 3.341e-4 m on B        (exact 3.333e-4)
tip ux (x = 2 m):     1.3336e-3 m                              (exact 1.3333e-3)
```

Three practical points from building it:

- **The Neumann block must be the stiffer one.** The Dirichlet–Neumann error factor
  is roughly E_B / E_A = 1/3 per iteration here; swap the stiffnesses and plain
  Gauss–Seidel diverges unless you add an accelerator.
- **Monitor a datum that is nonzero in the first iteration.** The
  `relative_norm_initial_residual` criterion divides by the first residual; if that is
  exactly zero the result is NaN and the step is reported as converged immediately.
  The example solves `structure_b` first (so the load arriving at `structure_a` is
  nonzero from iteration 1) and watches that load.
- **Results are indexed per solver.** Both blocks' VTK output is recorded in
  `result-index.json`, one series per model part, so
  `results_time_history(source=<job>/result-index.json, variable="DISPLACEMENT",
  point=[2, 0, 0], series="StructureB:vtk_output_StructureB:StructureB_0")` reads the tip
  displacement; without `series` the tool lists the available ones.

## Limits

CoSimulation cases run as ordinary background jobs, but not with
`mpi_ranks`, studies, checkpoints/resume or interactive sessions — each is
rejected with a clear message. Kratos-wrapped sub-solvers' VTK output is indexed
(one series per model part); Python-only SDoF solvers write their own result files.
Flowgraph export is refused rather than made lossy. The static validator checks
interface `model_part_name`s of Kratos-wrapped solvers against their meshes, but not
sub-model-parts that a process creates at run time.
