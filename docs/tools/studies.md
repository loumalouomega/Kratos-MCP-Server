# Parameter and mesh studies

Studies prepare isolated serial single-stage jobs, execute a bounded queue, and
collect named physical-location probe responses. A detached coordinator continues
after the MCP client or server exits. POSIX process groups and file locks are
required; MPI, restart-input, and multi-stage studies are not supported.

## `study_start`

*Needs Kratos.* Arguments:

| Argument | Meaning |
| --- | --- |
| `case_dir` | Absolute base-case directory; never modified |
| `specification` | Parameter axes or structured mesh resolutions, below |
| `responses` | Nonempty list of uniquely named probe responses |
| `max_concurrency` | Positive integer, default `1`; limit per study |
| `parameters_file` | Case-relative JSON filename, default `ProjectParameters.json` |
| `analysis_type`, `analysis_class` | Same optional dispatch overrides as `run_simulation` |
| `external_inputs` | Same absolute-source → case-relative-destination mappings as isolated runs |

All variants receive static case validation before any simulation launches.
Invalid paths, missing pointer targets, unsupported cases, and inconsistent
boundary names fail preparation. This is not a native solver validation or a
promise that all variants will solve. Native solver failures are recorded per job.
The call returns after preparation and coordinator launch, without waiting for
simulations. Every child is isolated; there is no `isolate=false` option.

### Parameter specification

```json
{
  "kind": "parameter",
  "combination": "product",
  "axes": [{
    "file": "Materials.json",
    "pointer": "/properties/0/Material/Variables/YOUNG_MODULUS",
    "values": [105000000000, 210000000000, 420000000000]
  }]
}
```

`file` names a JSON file in the frozen case, including mapped external inputs.
JSON Pointers replace existing values; array indices are zero-based, `~1`
escapes `/`, and `~0` escapes `~`. Root replacement, append operations,
duplicate targets, and overlapping parent/child targets are rejected.
Values retain their JSON types. `product` visits Cartesian combinations in axis
order, with the last axis varying fastest. `zip` pairs entries by index and
requires equal axis lengths. Empty axes/value lists are invalid.

### Mesh specification

```json
{
  "kind": "mesh",
  "mesh": {"kind": "rectangle", "size": [1.0, 0.2]},
  "divisions": [[4, 1], [8, 2], [16, 4]],
  "atol": 1e-8,
  "rtol": 1e-5,
  "time_atol": 1e-12
}
```

Use a single MDPA-import case. `mesh` accepts `kind` (`line`, `rectangle`,
`box`), `size`, and optional `element_name`, `condition_name`, and `triangles`,
with the same defaults as [structured mesh generation](/tools/mesh).
At least two division vectors are required. Sizes and integer divisions must
be positive; each resolution must increase at least one division without
reducing another. Geometry and element/condition options remain fixed.
Named boundaries come from the generator, so case processes must refer to those
names. Supplied arbitrary meshes and automatic remeshing are not included.

### Probe responses

```json
[{
  "name": "tip_y",
  "variable": "DISPLACEMENT",
  "association": "point",
  "point": [1.0, 0.0, 0.0],
  "component": 1,
  "reduction": "final",
  "max_distance": 1e-8
}]
```

`association` defaults to `point`; `cell` probes the nearest cell centroid.
Coordinates have two or three finite components. There is no interpolation.
An optional `max_distance` rejects samples too far from the requested point.
Omit `component` for a scalar field; vector fields require a zero-based component
index or `"magnitude"`. `reduction` is `final` (default), `min`, or `max` over
recorded physical times. `series` selects an output series when multiple exist.

Configure VTK output for each requested variable in the base case. The existing
runner writes `result-index.json`; missing indexes, fields, or invalid probes
produce response errors. They do not change a successful simulation into a
failed job. Histories retain the field value, reduced sample value, file,
physical time, chosen entity, coordinates, and distance from the query.

## Monitoring and lifecycle

| Tool | Arguments and behavior |
| --- | --- |
| `study_status` | `study_id`; metadata, variants, child job status, provenance, results/errors |
| `study_list` | Optional `state`; list persisted studies |
| `study_cancel` | `study_id`; stop launches and cancel active process groups |
| `study_resume` | `study_id`; resume an interrupted coordinator after verification |
| `study_results` | `study_id`, optional absolute `csv_file`; partial/complete results and comparisons |

Study states are `running`, `interrupted`, `succeeded`, `completed_with_errors`,
and `cancelled`. Child states remain `queued`, `running`, `succeeded`, `failed`,
or `cancelled`. Individual failures do not stop other variants. `job_status`,
`job_logs`, `job_cancel`, and snapshot-based `job_rerun` work on child job IDs.
A rerun is a separate job and does not replace the original study result.

Cancellation preserves completed results and cannot be resumed. If only the
coordinator dies, active jobs continue. `study_resume` adopts those identities
and launches remaining queued work; it never retries a failed child. Base and
variant snapshot inventories, pending execution inputs, and the Kratos build
fingerprint must match. A supervisor that dies after claiming a child causes
that child to fail; recovery terminates any surviving runner in its group.

Concurrency limits apply separately to each study and do not constrain manually
launched jobs. Thread environment settings are retained from submission. Choose
limits with the solver's OpenMP thread count in mind; automatic CPU allocation
and MPI resource controls are separate work.

## Results and comparisons

`study_results` returns variants in submission order with overrides or divisions,
mesh summaries, job details, and named responses. Partial results remain useful
while work is running. CSV exports one row per variant/response with its scalar
value or error and convergence differences. CSV creation is exclusive: an
existing file is never overwritten. Full histories remain in the JSON result.

Mesh responses are compared with the preceding resolution and the finest
**requested** resolution. A missing or failed finest response remains unavailable;
the reference never silently changes. Samples must match physical times uniquely
within `time_atol` (default `1e-12`). Comparisons report:

- absolute difference;
- relative difference against the reference magnitude, or `null` for a zero reference;
- `within_tolerance`, using `atol + rtol * abs(reference)` (defaults `1e-8`, `1e-5`).

The coarsest level has no previous comparison. Comparison errors are reported
locally; study success describes completed simulations and response extraction,
not satisfaction of the convergence tolerances. These are response differences,
not estimates of convergence order, extrapolated accuracy, or GCI.

See the [cantilever studies tutorial](/tutorials/studies) for runnable examples.
