---
layout: home

hero:
  name: Kratos MCP Server
  text: Finite element simulations for AI assistants
  tagline: >-
    Scaffold, run and post-process Kratos Multiphysics simulations through
    the Model Context Protocol — 30 tools, guided workflows, managed jobs.
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started
    - theme: alt
      text: Tool reference
      link: /tools/
    - theme: alt
      text: Cantilever tutorial
      link: /tutorials/cantilever-beam

features:
  - icon: 🔍
    title: Deep introspection
    details: >-
      List applications, elements, conditions, constitutive laws, variables
      and solver default parameters straight from your Kratos build and its
      source tree.
  - icon: 🏗️
    title: Case scaffolding
    details: >-
      Templates for structural (static, dynamic, modal), thermal and fluid
      analyses. Structured mesh generation with named boundary regions,
      boundary conditions and loads as one-call edits.
  - icon: 🚀
    title: Managed simulation jobs
    details: >-
      Simulations run as detached background jobs with status, live logs,
      progress parsing and cancellation. Jobs survive server restarts, and a
      solver crash can never take the server down.
  - icon: 📊
    title: Post-processing built in
    details: >-
      Discover result files, summarise VTK fields, probe values at points,
      and analyse nonlinear convergence from the solver logs.
  - icon: 🛡️
    title: Crash-proof architecture
    details: >-
      Kratos never runs inside the server process. Every Kratos operation is
      isolated in a subprocess with the right environment injected.
  - icon: ✅
    title: Validation before you run
    details: >-
      Dry-run case validation cross-checks parameters, mesh submodelparts,
      materials and solver settings against Kratos defaults — catching
      mistakes before the time loop starts.
---
