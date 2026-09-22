# Changelog

Notable changes to Kratos MCP Server. Historical entries are reconstructed
from repository tags and diffs; dates are the tagged commits' dates.

## Unreleased

## 0.5.0 — 2026-09-23

### Added

- Persistent parameter sweeps (JSON Pointer product/zip axes) and structured
  mesh-convergence studies, with six MCP tools, isolated provenance, bounded
  detached execution, verified coordinator recovery, probe responses and CSV.
- Durable queued study jobs with exclusive supervisor ownership, process-group
  cancellation, and recorded exit status independent of the MCP server lifetime.
- Cantilever study tutorial and numerical stiffness/refinement acceptance tests;
  the completed study item is removed from the roadmap, leaving MPI next.

- Managed checkpoint configuration, discovery, and verified resume into a new
  isolated serial single-stage job, with a structural dynamics acceptance test.
- Physical-time VTK indexes, point/cell time histories, CSV export, and
  tolerance-based history and matching-mesh field comparisons.

- This changelog and a prioritized roadmap with implementation gaps,
  Kratos source references, and acceptance probes.
- Roadmap links in the README and documentation navigation.
- Per-stage deep validation for orchestrated cases, with stage-qualified
  errors and explicit warnings when an optional solver application is absent.
- Versioned job manifests, SHA-256 input inventories, opt-in isolated case
  snapshots, explicit external-input mappings, and verified snapshot reruns.
- A runnable NACA0012 perturbation potential-flow fixture with upstream
  reference data, a resource, tutorial, and capability-gated integration test.

## 0.4.0 — 2026-09-19

### Changed

- Refreshed Python and documentation dependency locks, including anyio
  4.15.1, and updated Vite, esbuild, and PostCSS overrides.
- Synchronized package versions at 0.4.0.

## 0.3.1 — 2026-08-07

### Changed

- Refreshed the Python dependency lock and bumped package versions to 0.3.1.

## 0.3.0 — 2026-07-14

### Added

- Multi-stage project scaffolding and execution through Kratos' native
  Project and orchestrator, including shared model-part reuse.
- Process-default discovery through Python AST parsing, with defaults used
  when adding boundary conditions and output processes.
- Material and linear-solver presets, fractional-step fluid and potential-flow
  templates, and structured ProjectParameters explanations.
- Lossless ProjectParameters ↔ FlowGraph conversion tools.
- NACA0012 airfoil, lid-driven cavity, plasticity cube, and multi-stage
  examples; additional example resources, tutorials, and client notebooks.
- Visualization crop bounds for focusing on small bodies in large domains.

### Changed

- Updated FlowGraph links to its official KratosMultiphysics repository.
- Added license metadata to the documentation package.

## 0.2.0 — 2026-07-12

### Added

- Optional PyVista PNG rendering and GIF animation through isolated worker
  processes, with headless Xvfb support and visualization documentation.
- A bundled cantilever case with real mesh, parameters, materials, and
  integration checks.
- The `kratos-mcp-server` command alias alongside `kratos-mcp`.

### Changed

- Raised the MCP SDK minimum to 1.10 for tools returning images and metadata.

## 0.1.1 — 2026-07-12

### Changed

- Renamed the Python distribution from `kratos-mcp` to `kratos-mcp-server`.

## 0.1.0 — 2026-07-12

### Added

- Initial stdio MCP server with subprocess-isolated Kratos introspection,
  build-tree discovery, pip fallback, and installation tooling.
- Structural, thermal, and fluid case templates, materials and process
  editing, structured MDPA mesh generation, inspection, and validation.
- Background simulation jobs with persistent status, logs, progress, and
  cancellation, plus VTK summaries, probes, and convergence analysis.
- Example resources, workflow prompts, documentation, tests, and CI,
  documentation deployment, and release workflows.
