# Cantilever stiffness and mesh studies

Start with the [cantilever case](/tutorials/cantilever-beam). It must contain
`ProjectParameters.json`, `Materials.json`, and `mesh.mdpa`, with DISPLACEMENT
VTK output enabled. The bundled case uses a 1 m × 0.2 m plate, fixed on the left
and loaded on the right. StructuralMechanicsApplication and
LinearSolversApplication must be available in the configured build.

When working from this repository, copy the bundled case to your work directory:

```bash
cp -r src/kratos_mcp/examples/cantilever /tmp/cantilever-study
```

The following are MCP tool arguments. Replace `/tmp/cantilever-study` if you
used a different absolute path.

## Sweep Young's modulus

Call `study_start`:

```json
{
  "case_dir": "/tmp/cantilever-study",
  "max_concurrency": 2,
  "specification": {
    "kind": "parameter",
    "axes": [{
      "file": "Materials.json",
      "pointer": "/properties/0/Material/Variables/YOUNG_MODULUS",
      "values": [105000000000, 210000000000, 420000000000]
    }]
  },
  "responses": [{
    "name": "tip_y",
    "variable": "DISPLACEMENT",
    "point": [1.0, 0.0, 0.0],
    "component": 1,
    "reduction": "final",
    "max_distance": 1e-8
  }]
}
```

Use the returned `study_id` with `study_status` or `study_results`. All three
responses should be negative; doubling stiffness halves the displacement
magnitude. Each variant has a distinct child job, snapshot, and execution
directory. The original case is unchanged. A solver failure appears alongside
successful variants rather than discarding their results.

## Refine the mesh

Call `study_start` again with the same case and response, replacing
`specification` with:

```json
{
  "kind": "mesh",
  "mesh": {
    "kind": "rectangle",
    "size": [1.0, 0.2]
  },
  "divisions": [[4, 1], [8, 2], [16, 4]]
}
```

These meshes have 10, 27, and 85 nodes and preserve the named left/right/top/bottom
boundaries. The tip response changes with resolution. The difference between
8 × 2 and 16 × 4 should be smaller than between 4 × 1 and 16 × 4; this is checked
by the real-build integration test. It does not establish mesh independence by
itself. Choose tolerances appropriate to the response and physical problem.

`study_results` includes previous-level and finest-level differences. To export
scalar results, pass `csv_file` with a new absolute filename, for example
`/tmp/cantilever-mesh-responses.csv`. Full histories and sampled coordinates stay
available through the JSON response.

## Disconnect, recover, or cancel

Closing the MCP connection leaves the coordinator running. Reconnect and use
`study_list` to discover existing studies. If a coordinator crashes, its study
becomes `interrupted`; `study_resume` verifies the stored inputs and adopts any
still-running child jobs before continuing the queue. Use `study_cancel` to stop
both pending work and active jobs while keeping completed results.
