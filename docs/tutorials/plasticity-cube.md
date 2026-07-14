# Tutorial: single-element plasticity

The smallest possible demonstration of a **nonlinear** constitutive law. One
1×1×1 m hexahedral element is stretched past its yield point under von Mises
(J2) plasticity, and the reaction force plateaus at the yield stress — the
material yields instead of carrying more load. Every number below comes from a
real run against Kratos 10.4 with the ConstitutiveLawsApplication.

The `kratos://examples/plasticity-cube` resource is this exact case with its
files and result baked in, and `notebooks/materials.ipynb` walks through it as a
live MCP client (alongside the material- and solver-preset tools).

## 1. Generate the mesh

A single hexahedron is `create_box_mesh` with one division per axis — 8 nodes,
1 element, with `xmin`/`xmax`/`ymin`/… face sub-model-parts:

```json
mdpa_create_structured_mesh({
  "path": "/tmp/plast/mesh.mdpa", "kind": "box",
  "size": [1.0, 1.0, 1.0], "divisions": [1, 1, 1]
})
→ { "num_nodes": 8, "num_elements": 1 }
```

## 2. Build the material from a preset

`create_materials` accepts a `preset` — a ready-made constitutive law plus
default variables. `small_strain_plasticity_von_mises_3d` fills the von Mises
plasticity law (E = 210 GPa, yield stress = 250 MPa, perfect plasticity):

```json
create_materials({ "output_file": "/tmp/plast/Materials.json", "materials": [
  { "model_part_name": "Structure.domain",
    "preset": "small_strain_plasticity_von_mises_3d" }
]})
```

List everything available with `list_material_presets`; discover a process'
parameters with `kratos_get_process_defaults`.

## 3. The ProjectParameters

Plasticity is nonlinear, so the solver needs `analysis_type: "non_linear"` (a
Newton–Raphson loop with a residual convergence criterion) — this is the one
example whose ProjectParameters is hand-authored rather than rendered from the
linear `structural_static` template. Symmetry (roller) supports on the
`xmin`/`ymin`/`zmin` faces and a prescribed x-displacement ramped on `xmax`:

```json
"processes": { "constraints_process_list": [
  { "...": "fix ux=0 on xmin, uy=0 on ymin, uz=0 on zmin (rollers)" },
  { "python_module": "assign_vector_variable_process", "Parameters": {
      "model_part_name": "Structure.xmax", "variable_name": "DISPLACEMENT",
      "value": ["0.001*t", null, null], "constrained": [true, false, false] } }
]}
```

## 4. Run

```json
run_simulation({ "case_dir": "/tmp/plast", "wait_seconds": 60 })
→ { "state": "succeeded", "elapsed_seconds": 1.0 }
```

Five steps ramp the imposed x-displacement from 0.001 m to 0.005 m.

## 5. The elastic → plastic transition

Read the reaction force in x on the fixed `xmin` face at each step:

```
step 1  strain 0.001  |Fx| = 2.10e8 N   ← elastic: E·strain·A = 210 MPa
step 2  strain 0.002  |Fx| = 2.50e8 N   ← yielded
step 3  strain 0.003  |Fx| = 2.50e8 N   ← plateau
step 4  strain 0.004  |Fx| = 2.50e8 N
step 5  strain 0.005  |Fx| = 2.50e8 N
```

Below the yield strain (σ_y/E = 250e6/210e9 = 0.00119) the response is linear
elastic and |Fx| = E·strain·A exactly. Once past yield the reaction plateaus at
|Fx| = σ_y·A = 250 MPa × 1 m² = **2.5e8 N** no matter how much further you
stretch it. A `LinearElastic3DLaw` would keep climbing; the plateau *is* the
nonlinearity.

## Variations

- **Other yield surfaces** — the preset uses Von Mises; the constitutive-laws
  catalog (`kratos_list_constitutive_laws`) also has Drucker–Prager,
  Mohr–Coulomb and Tresca variants.
- **Damage instead of plasticity** — swap to the
  `small_strain_damage_von_mises_3d` preset for stiffness degradation.
- **Hardening** — set `HARDENING_CURVE` to a hardening law so the reaction keeps
  rising (gently) after yield instead of plateauing flat.
