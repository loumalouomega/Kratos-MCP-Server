# Tutorial: cantilever beam

A 2D cantilever plate — 1 m long, 0.2 m tall steel, fixed on the left edge,
with a 1 MN/m downward line load on the right edge — solved end to end
through MCP tool calls. Every call and every number below comes from a real
run against Kratos 10.4.

You can hand the whole thing to an assistant as one request:

> Set up a 1 m × 0.2 m steel cantilever fixed on the left with a 1 MN/m
> downward line load on the right edge. Run it and compare the tip
> deflection with beam theory.

The same prompt works in both **Claude Code** and **GitHub Copilot Chat in
VS Code** (Agent mode) — see [Connecting a client](/guide/connecting) for
setup. The two differ only in how tool calls are surfaced:

- **Claude Code** shows each tool call and its result inline as it happens;
  no confirmation is needed unless your permission settings require it.
- **GitHub Copilot Chat** requires **Agent mode** (the mode dropdown above
  the chat box — MCP tools aren't available in Ask/Edit mode) and asks you
  to approve the *first* call to each tool per workspace (a one-time click
  per tool, not per call).

Want something to inspect or paste in without running any tools at all? The
`kratos://examples/cantilever` MCP resource is a smaller, literal version of
this same setup (a 4×1 mesh instead of 20×4) with the exact file contents
and a pre-verified result baked in — ask either assistant to *"read the
kratos cantilever example resource"*, or fetch it yourself with an MCP
inspector.

What follows is the tool-by-tool walkthrough for the full 20×4 case.

## 1. Check the environment

```json
kratos_check_installation()
→ { "importable": true, "version": "10.4.2-...", 
    "compiled_applications": ["StructuralMechanicsApplication", "LinearSolversApplication", "..."] }
```

Both required applications are compiled.

## 2. Generate the mesh

A structured 20 × 4 quad mesh. The defaults are already right for structural
cases: `SmallDisplacementElement2D4N` elements and — crucially —
`LineLoadCondition2D2N` boundary conditions that can carry loads.

```json
mdpa_create_structured_mesh({
  "path": "/tmp/cantilever/mesh.mdpa",
  "kind": "rectangle", "size": [1.0, 0.2], "divisions": [20, 4]
})
→ { "num_nodes": 105, "num_elements": 80, "num_conditions": 48,
    "sub_model_parts": { "domain": "...", "left": "...", "right": "...", "top": "...", "bottom": "..." } }
```

## 3. Scaffold the case

The `structural_static` template defaults to steel
(E = 210 GPa, ν = 0.3, plane strain) and fixes `Structure.left` — exactly
our support.

```json
create_project({
  "directory": "/tmp/cantilever",
  "template": "structural_static",
  "name": "cantilever"
})
→ { "created": ["/tmp/cantilever/ProjectParameters.json", "/tmp/cantilever/Materials.json"] }
```

## 4. Apply the load

```json
add_boundary_condition({
  "parameters_file": "/tmp/cantilever/ProjectParameters.json",
  "kind": "line_load",
  "model_part": "Structure.right",
  "modulus": 1000000.0,
  "direction": [0.0, -1.0, 0.0]
})
→ { "process_list": "loads_process_list", "added": "..." }
```

## 5. Validate and run

```json
validate_case({"case_dir": "/tmp/cantilever"})
→ { "valid": true, "issues": [] }

run_simulation({"case_dir": "/tmp/cantilever", "wait_seconds": 120})
→ { "job_id": "...", "state": "succeeded", "elapsed_seconds": 2.0 }
```

## 6. Post-process

```json
results_list({"case_dir": "/tmp/cantilever"})
→ { "results": { "vtk": ["vtk_output/Structure_0_1.vtk"] } }

results_summary({"file": "/tmp/cantilever/vtk_output/Structure_0_1.vtk"})
→ { "statistics": { "DISPLACEMENT": { "max_magnitude": 4.32e-4 } } }

results_probe({
  "file": "/tmp/cantilever/vtk_output/Structure_0_1.vtk",
  "variable": "DISPLACEMENT", "point": [1.0, 0.1, 0.0]
})
→ { "value": [-1.6e-18, -4.272e-4, 0.0] }
```

**Tip deflection: 0.427 mm downward.**

## 7. Sanity check against beam theory

Total load `P = q·h = 10⁶ × 0.2 = 2×10⁵ N`. Euler–Bernoulli:

```
δ = P·L³ / (3·E·I),  I = h³/12 = 6.67×10⁻⁴ m⁴
δ = 2×10⁵ / (3 × 2.1×10¹¹ × 6.67×10⁻⁴) = 4.76×10⁻⁴ m
```

The FE result (4.27×10⁻⁴ m) is ~10% below the slender-beam estimate —
expected for a beam this deep (L/h = 5), where shear deformation and the
plane-strain constitutive law matter. The integration test
`tests/test_run_integration.py::test_structural_cantilever` asserts this
agreement automatically.

## Variations

- **Gravity instead of an edge load**: `add_boundary_condition` with
  `kind: "self_weight"` on `Structure.domain`.
- **Dynamics**: template `structural_dynamic`, then watch `job_status`
  progress over the time steps.
- **Natural frequencies**: template `structural_modal`
  (`num_eigenvalues` placeholder); eigenvalues appear in `job_logs`.
- **3D**: `kind: "box"` mesh + `overrides: {"domain_size": 3,
  "constitutive_law": "LinearElastic3DLaw",
  "fix_model_part": "Structure.xmin"}`, load on `Structure.xmax`
  with `kind: "surface_load"`.
