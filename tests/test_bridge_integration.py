"""Integration tests for the bridge/worker protocol against a real Kratos build."""

from __future__ import annotations

import pytest

from kratos_mcp import bridge, mdpa

pytestmark = pytest.mark.kratos


def test_check_op():
    result = bridge.run_op("check", use_cache=False)
    assert result["importable"] is True
    assert "compiled_applications" in result


def test_list_variables_cached():
    result = bridge.run_op("list_variables")
    assert "DISPLACEMENT" in result["array_1d_3"]
    assert "TEMPERATURE" in result["double"]
    # second call hits the disk cache (no way to observe directly; just correctness)
    assert bridge.run_op("list_variables") == result


def test_get_solver_defaults():
    result = bridge.run_op("get_solver_defaults", {
        "module": "KratosMultiphysics.StructuralMechanicsApplication."
                  "structural_mechanics_static_solver"})
    assert "solver_type" in result
    assert "linear_solver_settings" in result
    assert "model_import_settings" in result


def test_has_constitutive_laws():
    result = bridge.run_op("has_constitutive_laws", {
        "names": ["LinearElasticPlaneStrain2DLaw", "NotALaw"],
        "applications": ["StructuralMechanicsApplication"]})
    assert result["LinearElasticPlaneStrain2DLaw"] is True
    assert result["NotALaw"] is False


def test_read_mdpa_deep(tmp_path):
    path = mdpa.create_rectangle_mesh(1.0, 0.2, 4, 2).write(tmp_path / "m.mdpa")
    result = bridge.run_op("read_mdpa_deep", {"path": str(path)})
    assert result["read_ok"] is True
    assert result["model_part"]["nodes"] == 15
    assert result["model_part"]["sub_model_parts"]["left"]["nodes"] == 3


def test_unknown_op_raises():
    with pytest.raises(bridge.BridgeError, match="Unknown op"):
        bridge.run_op("not_an_op")


def test_validate_parameters_op(tmp_path):
    (tmp_path / "p.json").write_text('{"problem_data": {}, "solver_settings": {}}')
    result = bridge.run_op("validate_parameters", {"parameters_file": str(tmp_path / "p.json")})
    assert result["valid"] is True

    (tmp_path / "bad.json").write_text('{"problem_data": {}}')
    result = bridge.run_op("validate_parameters", {"parameters_file": str(tmp_path / "bad.json")})
    assert result["valid"] is False
