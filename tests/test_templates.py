from __future__ import annotations

import json

import pytest

from kratos_mcp.tools import scaffold


def test_registry_templates_render_to_valid_json():
    registry = scaffold.load_registry()
    assert registry, "registry must not be empty"
    for name, meta in registry.items():
        values = meta["placeholders"]
        pp = json.loads(scaffold.render_template_file(name, "ProjectParameters.json", values))
        assert "problem_data" in pp and "solver_settings" in pp, name
        mats = json.loads(scaffold.render_template_file(name, "Materials.json", values))
        assert "properties" in mats, name


def test_render_typed_substitution():
    out = scaffold.render(
        '{"a": "{{num}}", "b": "{{arr}}", "c": "pre-{{word}}-post"}',
        {"num": 1.5, "arr": [1, 2], "word": "x"})
    assert json.loads(out) == {"a": 1.5, "b": [1, 2], "c": "pre-x-post"}


def test_render_missing_placeholder_raises():
    with pytest.raises(KeyError):
        scaffold.render('{"a": "{{missing}}"}', {})


def test_resolve_values_rejects_unknown_override():
    with pytest.raises(KeyError):
        scaffold._resolve_values("structural_static", {"not_a_placeholder": 1})


def test_infer_solver_module():
    params = {"solver_settings": {"solver_type": "Static"}}
    assert "structural_mechanics_static_solver" in scaffold.infer_solver_module(params)
    params = {"solver_settings": {"solver_type": "stationary"}}
    assert "convection_diffusion_stationary_solver" in scaffold.infer_solver_module(params)
    assert scaffold.infer_solver_module({"solver_settings": {"solver_type": "nope"}}) is None


def test_validate_case_files_static_checks(tmp_path):
    # missing parameters file
    result = scaffold.validate_case_files(tmp_path, deep=False)
    assert not result["valid"]

    # scaffold a full case without mesh -> mesh missing error
    values = scaffold._resolve_values("structural_static", None)
    (tmp_path / "ProjectParameters.json").write_text(
        scaffold.render_template_file("structural_static", "ProjectParameters.json", values))
    (tmp_path / "Materials.json").write_text(
        scaffold.render_template_file("structural_static", "Materials.json", values))
    result = scaffold.validate_case_files(tmp_path, deep=False)
    assert not result["valid"]
    assert any("mesh.mdpa" in i for i in result["issues"])

    # add the mesh -> static checks pass
    from kratos_mcp import mdpa
    mdpa.create_rectangle_mesh(1.0, 0.2, 4, 2).write(tmp_path / "mesh.mdpa")
    result = scaffold.validate_case_files(tmp_path, deep=False)
    assert result["valid"], result["issues"]


def test_validate_case_files_detects_bad_model_part(tmp_path):
    from kratos_mcp import mdpa

    values = scaffold._resolve_values(
        "structural_static", {"fix_model_part": "Structure.nonexistent"})
    (tmp_path / "ProjectParameters.json").write_text(
        scaffold.render_template_file("structural_static", "ProjectParameters.json", values))
    (tmp_path / "Materials.json").write_text(
        scaffold.render_template_file("structural_static", "Materials.json", values))
    mdpa.create_rectangle_mesh(1.0, 0.2, 4, 2).write(tmp_path / "mesh.mdpa")
    result = scaffold.validate_case_files(tmp_path, deep=False)
    assert not result["valid"]
    assert any("nonexistent" in i for i in result["issues"])
