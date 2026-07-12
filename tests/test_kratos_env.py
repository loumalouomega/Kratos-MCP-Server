from __future__ import annotations

from kratos_mcp import kratos_env


def test_pypi_package_name_prefixes_kratos():
    assert kratos_env.pypi_package_name("StructuralMechanicsApplication") == \
        "KratosStructuralMechanicsApplication"
    assert kratos_env.pypi_package_name("ConvectionDiffusionApplication") == \
        "KratosConvectionDiffusionApplication"


def test_pypi_package_name_idempotent_if_already_prefixed():
    assert kratos_env.pypi_package_name("KratosLinearSolversApplication") == \
        "KratosLinearSolversApplication"


def test_pip_install_builds_correct_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(kratos_env.subprocess, "run", fake_run)
    kratos_env.pip_install(["KratosMultiphysics", "KratosStructuralMechanicsApplication"])

    cmd = captured["cmd"]
    assert cmd[0] == kratos_env.sys.executable
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert cmd[4:] == ["KratosMultiphysics", "KratosStructuralMechanicsApplication"]
    assert "--upgrade" not in cmd


def test_pip_install_upgrade_flag(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(kratos_env.subprocess, "run", fake_run)
    kratos_env.pip_install(["KratosMultiphysics"], upgrade=True)
    assert "--upgrade" in captured["cmd"]


def test_all_pypi_package_constant():
    assert kratos_env.PYPI_ALL_PACKAGE == "KratosMultiphysics-all"
    assert kratos_env.PYPI_CORE_PACKAGE == "KratosMultiphysics"
