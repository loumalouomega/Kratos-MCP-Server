# NACA0012 potential flow

This tutorial runs the small NACA0012 perturbation-compressible benchmark from Kratos' CompressiblePotentialFlowApplication through the MCP job tools. The repository includes the mesh, parameters, and upstream nodal reference in `src/kratos_mcp/examples/potential_flow/`.

The case needs both `CompressiblePotentialFlowApplication` and `LinearSolversApplication`. Check the installation with `kratos_list_applications`, copy the three files from the example directory to a case directory, and start it with:

```text
run_simulation(case_dir="case", wait_seconds=120)
```

The benchmark should report lift coefficient `0.4968313580730855` and a potential jump of `0.48769319614651147` at node 13, within `1e-6`. Its `auxiliar_process_list` independently checks the nodal velocity potential from `reference_velocity_potential.json`.

The fixture is copied from the [Kratos source revision](https://github.com/KratosMultiphysics/Kratos/tree/e740da832999e4da58dd9457f35143274edb4992/applications/CompressiblePotentialFlowApplication/tests) `e740da832999e4da58dd9457f35143274edb4992`. It is kept separate from the incompressible NACA airfoil example because the two use different solvers and boundary-process conventions.
