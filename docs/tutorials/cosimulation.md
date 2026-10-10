# Tutorial: coupled solvers (CoSimulation)

Some problems need several solvers advancing together and trading data every
time step — fluid and structure, two thermal domains, or here the simplest
possible stand-in: two oscillators joined by a spring. Kratos'
CoSimulationApplication drives these; this server scaffolds, validates and runs
them. Every number below comes from a real run against Kratos 10.4 with
`KratosCoSimulationApplication` installed.

The `kratos://examples/coupled-sdof` resource is this exact case.

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
compare it against a single two-DoF solve.

## 5. Real solvers on two meshes

With Kratos solvers the same scaffold applies: each solver is a
`solver_wrappers.kratos.*` wrapper with its own `ProjectParameters`-style file
and mesh, and the interface `model_part_name`s point at sub-model-parts of each
solver's own mesh. `kratos://examples/coupled-blocks` is the worked case: two
1 m x 1 m elastic blocks split at x = 1, `block_a` clamped and receiving
`POINT_LOAD`, `block_b` loaded in tension and receiving an imposed
`DISPLACEMENT` (Dirichlet–Neumann). The interfaces do not match — 5 nodes
against 7 — so `data_transfer_operators` uses `kratos_mapping`
(`nearest_element`): the displacement is mapped with the mapper itself, the
reaction back with `["swap_sign", "use_transpose"]`, which makes the force
transfer conservative.

```json
validate_case({ "case_dir": "/tmp/blocks" })
→ { "valid": false, "issues": [
    "solver 'block_a': data 'load' model_part_name 'BlockA.interface' is not in the
     solver's mesh (root 'BlockA', SubModelParts: ['bottom', 'domain', 'left', 'right', 'top'])" ] }
```

The static validator reads both meshes, so a misspelt interface is reported
before launch instead of aborting `Initialize`. After the run:

    13 coupling iterations per step, 2 steps, all converged
    force through the interface at t = 1: 999998.2 N (applied: 1e6 N)
    response at t = 1 is 2.000 x the response at t = 0.5

`results_time_history(source="<case>/result-index.json",
variable="DISPLACEMENT", series="BlockB:vtk_output_block_b:BlockB_0",
point=[2, 0, 0])` reads the sub-solver output — each Kratos-wrapped solver is
its own series, and a call without `series` lists them.

Pitfalls the example encodes (and its resource text spells out): the receiving
block's interface needs `PointLoadCondition2D1N` conditions; vector data needs
`"dimension"`; put the convergence criterion and accelerator on the data the
*Neumann* side produces, because the Dirichlet side's first residual is 0/0 and
is reported as converged.

## Limits

CoSimulation cases run as ordinary background jobs, but not with
`mpi_ranks`, studies, checkpoints/resume or interactive sessions — each is
rejected with a clear message. Only Kratos-wrapped sub-solvers write VTK output
that is indexed (the SDoF wrappers write `.dat` files, read those directly).
Flowgraph export is refused rather than made lossy. The static validator checks
interface model parts against the meshes of Kratos-wrapped solvers; it does not
check that the variables exist in the solver's applications (Kratos does, at
initialisation).
