# Environment & introspection

Discover what your Kratos installation provides. Catalog tools (elements,
conditions, constitutive laws, processes) parse the Kratos C++/Python
sources; runtime tools query the live build through the
[worker bridge](/guide/architecture#the-bridge-short-operations).

## kratos_check_installation

*needs Kratos (gracefully reports when unavailable)*

Verify that Kratos is importable and report the environment.

**Parameters**: none.

**Returns**: `kratos_root`, `pythonpath`, `ld_library_path`, `pip_installed`,
`importable`, `version`, `build_type`, `num_threads`, `is_distributed`,
`compiled_applications` (list). When Kratos is missing, `importable: false`
plus a `hint`.

```json
{
  "importable": true,
  "version": "10.4.2-...-Release-x86_64",
  "num_threads": 20,
  "compiled_applications": ["ConvectionDiffusionApplication", "FluidDynamicsApplication", "..."]
}
```

When Kratos is missing, the `hint` field points at `kratos_install`.

## kratos_install

Install Kratos Multiphysics via pip into the server's own Python
environment — no local build required. Official wheels exist for **Linux
and Windows x86_64 only**; on macOS or other unsupported platforms this
fails and you need a local `KRATOS_ROOT` build instead (see
[Installation](/guide/installation)).

| Parameter | Type | Description |
| --- | --- | --- |
| `applications` | string[]? | application names to install alongside the core, e.g. `["StructuralMechanicsApplication", "LinearSolversApplication"]` — each is mapped to its PyPI name by prefixing `Kratos` (so `StructuralMechanicsApplication` → `KratosStructuralMechanicsApplication`) |
| `all` | bool | install the `KratosMultiphysics-all` omnibus package (essentially every application) instead of naming individual ones; largest download, simplest choice (default `false`) |
| `upgrade` | bool | pass `--upgrade` to pip (default `false`) |

**Returns**: `{ok, packages, returncode}`; on failure `stderr_tail` and a
`hint`; on success a nested `check` — the same payload
`kratos_check_installation` returns, so a single call installs and confirms.
No server restart is needed — the very next tool call sees the new install.

```json
// kratos_install(applications=["StructuralMechanicsApplication", "LinearSolversApplication"])
{
  "ok": true,
  "packages": ["KratosMultiphysics", "KratosStructuralMechanicsApplication", "KratosLinearSolversApplication"],
  "returncode": 0,
  "check": { "importable": true, "version": "...", "compiled_applications": ["StructuralMechanicsApplication", "..."] }
}
```

::: tip Which applications to install
Match them to the template you're using — `kratos_list_solvers` and
`list_templates` name the `required_applications` for each analysis type.
`LinearSolversApplication` is needed by essentially every template (it
provides `sparse_lu`, the default linear solver).
:::

## kratos_list_applications

List every application in the source tree with a `compiled` flag.

**Parameters**: none.
**Returns**: `{applications: [{name, compiled}], num_compiled, num_source}`.

## kratos_list_elements

List element type names parsed from `KRATOS_REGISTER_ELEMENT` macros.

| Parameter | Type | Description |
| --- | --- | --- |
| `application` | string? | e.g. `StructuralMechanicsApplication` (or `Core`) |
| `name_filter` | string? | case-insensitive substring, e.g. `SmallDisplacement` |

**Returns**: `[{name, application, compiled}]`.

## kratos_list_conditions

Same parameters/shape as `kratos_list_elements`, for conditions (the
boundary entities used by loads and fluxes — see the
[MDPA guide](/guide/mdpa-format#naming-conventions) for the crucial
load-condition vs geometric-condition distinction).

## kratos_list_constitutive_laws

Same parameters/shape, for material models (`LinearElastic3DLaw`,
`Newtonian2DLaw`, ...).

## kratos_list_variables

*needs Kratos*

List Kratos variables grouped by type, from the live kernel.

| Parameter | Type | Description |
| --- | --- | --- |
| `type_filter` | string? | one of `double`, `array_1d_3`, `bool`, `int`, `vector`, `matrix`, ... |
| `name_filter` | string? | substring, e.g. `TEMP` |

**Returns**: `{type: [names]}` — e.g. `{"array_1d_3": ["DISPLACEMENT", ...]}`.

## kratos_list_solvers

List known `solver_type` values per analysis type with the Python module
implementing each, plus all `*_solver.py` modules found in the source tree.

| Parameter | Type | Description |
| --- | --- | --- |
| `analysis_type` | string? | `structural`, `thermal`, `fluid`, `potential_flow` |

## kratos_list_processes

List Python process modules usable as `python_module` in ProjectParameters
process lists.

| Parameter | Type | Description |
| --- | --- | --- |
| `application` | string? | filter by owning application (or `Core`) |
| `name_filter` | string? | substring, e.g. `assign_vector` |

## kratos_get_solver_defaults

*needs Kratos*

Return the complete default `solver_settings` for a solver, straight from
its `GetDefaultParameters()` — the authoritative schema of every accepted
key.

| Parameter | Type | Description |
| --- | --- | --- |
| `analysis_type` | string | `structural` / `thermal` / `fluid` / `potential_flow` |
| `solver_type` | string | a value from `kratos_list_solvers`, e.g. `Static`, `transient`, `Monolithic` |

```json
// kratos_get_solver_defaults("structural", "Static") → (excerpt)
{
  "solver_type": "mechanical_solver",
  "model_import_settings": { "input_type": "mdpa", "input_filename": "unknown_name" },
  "linear_solver_settings": {},
  "convergence_criterion": "residual_criterion",
  "max_iteration": 10
}
```
