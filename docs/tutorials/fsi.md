# Tutorial: fluid–structure interaction (FSI)

A fluid pushes on a flexible structure, the structure deforms, the deformed
structure changes the fluid domain. Kratos' FSIApplication solves this in a
*partitioned* way: separate fluid and structure solvers, plus a mesh solver that
moves the fluid mesh (ALE), iterated inside every time step until the interface
stops changing (Dirichlet–Neumann coupling with a convergence accelerator). This
tutorial scaffolds a small case, shows the mistakes the server catches before
launch, runs it, and then points at the real benchmark. Every number below comes
from a real run against Kratos 10.4.4 with the FSI, FluidDynamics,
StructuralMechanics, MeshMoving and Mapping applications installed.

FSI cases differ from [CoSimulation](/tutorials/cosimulation) ones in one
convenient way: `FsiAnalysis` keeps the ordinary `(model, parameters)`
constructor, so `run_simulation` needs nothing special — it recognises
`solver_type: "partitioned"` and runs it.

## The problem

A 2 × 1 channel of viscous fluid flows over an elastic floor 0.4 thick. The inlet
velocity ramps up to a parabolic profile of peak 1.0 over one second; the outlet
is at zero pressure. The flow pushes the floor down.

| Part | Mesh | Interface nodes |
| --- | --- | --- |
| fluid `FluidModelPart` | 30 × 12 triangles | 31 (`FluidModelPart.Interface`) |
| wall `Structure` | 20 × 4 quads | 21 (`Structure.Interface`) |

The two interface discretizations differ on purpose, so the mapper between them
does real work.

## 1. Scaffold the case

```json
create_fsi_project({ "directory": "/tmp/fsi" })
```

The defaults are the case above (fluid density and viscosity 1, wall
`E = 200`, `nu = 0.3`, static wall, `end_time` 2 s in steps of 0.1 s,
`MVQN_recursive` accelerator). It writes `ProjectParameters.json`, `fluid.mdpa`,
`structure.mdpa`, `MaterialsFluid.json`, `MaterialsStructure.json` and returns
the static validation. Open `ProjectParameters.json` and look at the
`solver_settings`:

- `fluid_solver_settings`, `structure_solver_settings`: complete ordinary solver
  settings, one per physics, each on its own root model part;
- `mesh_solver_settings`: `mesh_solver_structural_similarity` on the fluid model
  part — it propagates the interface motion into the fluid domain;
- `coupling_settings`: the interface pair (`mapper_settings`), the accelerator
  (`coupling_strategy_settings`), `nl_tol` and `nl_max_it`.

`explain_project_parameters("/tmp/fsi/ProjectParameters.json")` summarizes all of
this as `kind: "fsi"`.

## 2. Catch mistakes before launch

Kratos reports interface mistakes deep inside `Initialize`. `validate_case`
reads both meshes first. A very common one is naming the interface without its
root model part — it used to work in older Kratos versions:

```json
"fluid_interfaces_list": ["Interface"]
```

```
valid: false
issues: ["fluid interface 'Interface' must be a full model part name
          'FluidModelPart.<SubModelPart>' (a bare name makes Kratos fail with
          'not found as root-ModelPart')"]
```

Other checks: an interface sub model part that is missing from its mesh or empty;
a mapper that is not listed in `fluid_interfaces_list`/`structure_interfaces_list`;
fluid and structure sharing a root model part name (they live in one `Model`);
mismatched `domain_size`s; skin parts, process targets and material targets that
are not in the mesh; an unknown accelerator or a non-positive `nl_max_it`/`nl_tol`;
and — as a warning — fluid and structure interfaces that do not sit on the same
line at t = 0. With deep validation the five required applications are checked
against the installed build.

## 3. Run it

```json
run_simulation({ "case_dir": "/tmp/fsi", "wait_seconds": 60 })
```

It finishes in a few seconds. The log has one line per coupling iteration:

```
PartitionedFSIBaseSolver: FSI non-linear iteration = 1 |res|/sqrt(nDOFS) = 6.1e-04
PartitionedFSIBaseSolver: FSI non-linear iteration = 2 |res|/sqrt(nDOFS) = 7.8e-04
...
PartitionedFSIBaseSolver: FSI non-linear iteration convergence achieved in 5 iterations.
```

`results_convergence(job_id)` turns these into per-step records: 20 steps, all
converged, between 3 and 7 coupling iterations each (`residual_ratios` is the
`|res|/sqrt(nDOFS)` history of the step).

> **Always look at convergence.** When the coupling does not converge, Kratos
> prints `FSI non-linear maximum iterations N reached` and carries on — the job
> still ends `succeeded`. A step with `converged: false` (or NaN residuals in the
> log) means the results of that step are not trustworthy.

## 4. Check the physics

The VTK output (`vtk_output/fluid`, `vtk_output/structure`, every 5 steps) holds
`MESH_DISPLACEMENT` on the fluid side and `DISPLACEMENT` on the wall:

| Time | floor deflection (min `DISPLACEMENT_Y` on the interface) | inlet pressure (max) |
| --- | --- | --- |
| 0.4 s (flow still ramping) | −8.4e-3 | 7.0 |
| 0.8 s | −1.98e-2 | 14.6 |
| 2.0 s (settled) | −2.05e-2 | 14.7 |

- The wall moves down only, and the deflection stops changing once the inflow is
  steady: the iteration has found the steady fluid–structure balance.
- The structure's interface displacement and the fluid mesh's interface
  displacement agree: the minimum is the same to within 0.1 % even though the two
  meshes have different nodes — that difference is the mapping error.
- `results_render` on `Parts_Fluid_0_20.vtk` with `MESH_DISPLACEMENT` or
  `PRESSURE` shows the fluid mesh sagging into the floor's dent.

## 5. When the coupling diverges

Make the wall softer or the flow faster and the Dirichlet–Neumann iteration stops
converging: the log fills with `Fluid solver did not converge for step N` and
`|res|/sqrt(nDOFS) = nan`. The cheap fixes, in order: stiffen the wall, lower
the Reynolds number, try another `accelerator` (`MVQN`, `Relaxation`). A smaller
time step does *not* help: it makes the fluid's pressure response to wall
velocity stronger. The rule of thumb is
`wall_thickness * viscosity * length^2 / (E * fluid_height^3 * time_step)` well
below 1. Re-run `create_fsi_project` with new arguments to experiment — a run
takes seconds.

## 6. The real benchmark

`kratos://examples/fsi-flexible-flap` is the Mok benchmark from Kratos' own FSI
tests: a flexible flap in a channel, solved on an externally authored mesh (4556
fluid nodes, 505 structure nodes) with the same `partitioned` solver. The
resource carries the adapted ProjectParameters (interfaces as full model-part
names, VTK output) and the upstream control-node history. Running 3 s (30 steps,
under a minute) reproduces the reference mesh displacement at the two control
nodes to within about 0.4 % and the pressure to about 1 %; the flap tip moves
0.0152 by 3 s.

## Limits

FSI cases are serial/OpenMP and run as one job: `mpi_ranks`, studies,
checkpoints (Kratos' `FsiAnalysis` has no restart), interactive sessions and
Flowgraph export reject them with an explanatory error. Single-faced structures
and double-faced ones (two mappers, `Positive`/`Negative`) pass validation;
`partitioned_embedded` (embedded FSI) is recognised and validated but has no
scaffold or worked example yet.
