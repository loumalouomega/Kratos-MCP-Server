from __future__ import annotations

import pytest

from kratos_mcp import source_catalog

needs_source = pytest.mark.skipif(
    source_catalog._kratos_source() is None,
    reason="Kratos source tree not available")


@needs_source
def test_catalog_has_core_and_app_entries():
    catalog = source_catalog.build_catalog()
    element_names = {e["name"] for e in catalog["elements"]}
    assert "Element2D3N" in element_names
    assert "SmallDisplacementElement2D4N" in element_names
    cl_names = {e["name"] for e in catalog["constitutive_laws"]}
    assert "LinearElastic3DLaw" in cl_names


@needs_source
def test_list_entities_filters():
    hits = source_catalog.list_entities(
        "conditions", application="StructuralMechanicsApplication",
        name_filter="LineLoadCondition2D2N")
    assert any(h["name"] == "LineLoadCondition2D2N" for h in hits)
    assert all(h["application"] == "StructuralMechanicsApplication" for h in hits)


@needs_source
def test_compiled_flag():
    hits = source_catalog.list_entities(
        "elements", name_filter="Element2D3N",
        compiled_apps=["StructuralMechanicsApplication"])
    core = [h for h in hits if h["application"] == "Core"]
    assert core and all(h["compiled"] for h in core)


@needs_source
def test_list_applications_and_solvers():
    apps = source_catalog.list_source_applications()
    assert "StructuralMechanicsApplication" in apps
    solvers = source_catalog.list_solver_modules("StructuralMechanicsApplication")
    assert "structural_mechanics_static_solver" in solvers


@needs_source
def test_list_python_processes():
    procs = source_catalog.list_python_processes()
    modules = {p["module"] for p in procs}
    assert "assign_vector_variable_process" in modules
