# Tutorial: thermal bar

Steady-state heat conduction in a 1 m bar with the left end held at 100 °C
and the right end at 0 °C. The analytical solution is a straight line —
`T(x) = 100·(1−x)` — which makes this the perfect correctness check. All
numbers below are from a real run.

## 1. Generate the mesh

Thermal cases have two special rules (the tools' docstrings and the
`setup_thermal_analysis` prompt both encode them):

- use **generic simplex elements** (`Element2D3N`, `triangles: true`) — the
  convection-diffusion solver substitutes the physical element at import and
  its mesh checks reject quads;
- use `ThermalFace2D2N` boundary conditions.

```json
mdpa_create_structured_mesh({
  "path": "/tmp/bar/mesh.mdpa",
  "kind": "rectangle", "size": [1.0, 0.1], "divisions": [20, 2],
  "element_name": "Element2D3N", "condition_name": "ThermalFace2D2N",
  "triangles": true
})
→ { "num_nodes": 63, "num_elements": 80 }
```

## 2. Scaffold the case

`thermal_stationary` defaults to steel-like properties (k = 15 W/m·K) and
already fixes `ThermalModelPart.left` at 100 °C. It also sets
`element_replace_settings` to `LaplacianElement` — without that, Kratos's
"stationary" solver still behaves like one transient step.

```json
create_project({
  "directory": "/tmp/bar",
  "template": "thermal_stationary",
  "name": "bar"
})
```

To change the hot-end temperature or conductivity, pass `overrides`:
`{"fixed_temperature": 350.0, "conductivity": 45.0}`.

## 3. Fix the cold end

```json
add_boundary_condition({
  "parameters_file": "/tmp/bar/ProjectParameters.json",
  "kind": "fix_temperature",
  "model_part": "ThermalModelPart.right",
  "value": 0.0
})
```

## 4. Run

```json
run_simulation({"case_dir": "/tmp/bar", "wait_seconds": 120})
→ { "state": "succeeded" }
```

## 5. Check the profile

```json
results_probe({"file": "/tmp/bar/vtk_output/ThermalModelPart_0_1.vtk",
               "variable": "TEMPERATURE", "point": [0.5, 0.05, 0.0]})
→ { "value": 50.0 }

results_probe({"file": "...", "variable": "TEMPERATURE", "point": [0.25, 0.05, 0.0]})
→ { "value": 75.0 }
```

Exactly linear: 50 °C at the midpoint, 75 °C at the quarter point — the FE
solution reproduces the analytical profile to machine precision on this
mesh. The integration test
`tests/test_run_integration.py::test_thermal_bar_linear_profile` asserts the
whole field against `100·(1−x)`.

## Variations

- **Transient heating**: template `thermal_transient` with `end_time` /
  `time_step` overrides; probe the same points across the VTK time series to
  watch the profile develop.
- **Heat flux instead of fixed temperature**: `kind: "surface_heat_flux"` on
  a boundary part (needs the `ThermalFace2D2N` conditions the mesh already
  has), or `kind: "volume_heat_source"` on `ThermalModelPart.domain`.
- **Different material**: `overrides: {"conductivity": 400.0, "density":
  8960.0, "specific_heat": 385.0}` for copper.
