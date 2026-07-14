# Resources

MCP resources are read-only documents an assistant can pull into context.

| URI | Content |
| --- | --- |
| `kratos://docs/mdpa-format` | the MDPA mesh format guide |
| `kratos://docs/project-parameters` | ProjectParameters.json structure and process-block pattern |
| `kratos://docs/materials` | Materials.json structure + common constitutive laws |
| `kratos://examples/cantilever` | complete worked structural case: literal, verified mesh.mdpa + ProjectParameters.json + Materials.json + result, hardcoded (not rendered from templates at request time) |
| `kratos://examples/thermal-bar` | complete worked thermal case (mesh recipe + rendered ProjectParameters + Materials) |
| `kratos://examples/naca-airfoil` | complete worked fluid case: NACA0012 airfoil, real ~21k-node mesh (summarised, not embedded — too large) + ProjectParameters.json + Materials.json + verified Cd/Cl result |
| `kratos://templates/{name}` | the raw template files for any template from `list_templates` |
| `kratos://jobs/{job_id}/log` | live stdout/stderr of a simulation job |

The two templated resources (`templates/{name}`, `jobs/{job_id}/log`) accept
any template name / job id; the rest are static.

## When to use which

- **Format questions** ("what does a SubModelPart block look like?") → the
  `docs/` resources answer without any tool call.
- **Starting a new case type** → `examples/` show a full, runnable file set;
  `templates/{name}` shows the raw placeholders a template accepts.
- **Watching a long run** → `jobs/{id}/log` streams the full log, while the
  `job_logs` tool returns filtered tails.
