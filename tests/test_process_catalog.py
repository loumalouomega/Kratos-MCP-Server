from __future__ import annotations

import pytest

from kratos_mcp import process_catalog, source_catalog

needs_source = pytest.mark.skipif(
    source_catalog._kratos_source() is None,
    reason="Kratos source tree not available")


# --- Offline AST tests (no Kratos): lock the parse-processes.py port ---------

_STD_PROCESS = '''
import KratosMultiphysics as KM

def Factory(settings, Model):
    if not isinstance(settings, KM.Parameters):
        raise Exception("expected Parameters")
    return AssignScalarVariableProcess(Model, settings["Parameters"])

class AssignScalarVariableProcess(KM.Process):
    def __init__(self, Model, settings):
        KM.Process.__init__(self)
        default_settings = KM.Parameters("""
        {
            "help"            : "assign a scalar",   // comment tolerated
            "model_part_name" : "please_specify",
            "variable_name"   : "SPECIFY_VARIABLE",
            "interval"        : [0.0, 1e30],
            "constrained"     : true,
            "value"           : 0.0,
        }
        """)
        settings.ValidateAndAssignDefaults(default_settings)
'''

_CLASSMETHOD_PROCESS = '''
import KratosMultiphysics as KM
def Factory(settings, Model):
    return MyProc(Model, settings["Parameters"])
class MyProc(KM.Process):
    def __init__(self, Model, settings):
        settings.ValidateAndAssignDefaults(self.GetDefaultParameters())
    @classmethod
    def GetDefaultParameters(cls):
        return KM.Parameters("""{ "model_part_name": "x", "factor": 2.0 }""")
'''


def test_extract_default_settings_standard():
    d = process_catalog.extract_default_settings(_STD_PROCESS)
    assert d is not None
    assert d["variable_name"] == "SPECIFY_VARIABLE"
    assert d["value"] == 0.0
    assert d["constrained"] is True
    assert d["interval"] == [0.0, 1e30]


def test_extract_default_settings_classmethod_fallback():
    d = process_catalog.extract_default_settings(_CLASSMETHOD_PROCESS)
    assert d == {"model_part_name": "x", "factor": 2.0}


def test_extract_default_settings_graceful_none():
    assert process_catalog.extract_default_settings("x = 1") is None
    assert process_catalog.extract_default_settings("def f(: syntax error") is None


def test_param_types():
    types = process_catalog.param_types(
        {"a": True, "b": 1, "c": 1.5, "d": "s", "e": [1], "f": {}, "g": None})
    assert types == {"a": "bool", "b": "number", "c": "number", "d": "string",
                     "e": "array", "f": "json", "g": "null"}


def test_split_params_model_part_inputs():
    defaults = {"help": "h", "model_part_name": "x",
                "computing_model_part_name": "skip", "variable_name": "V", "value": 0.0}
    help_text, inputs, others = process_catalog._split_params(defaults)
    assert help_text == "h"
    assert inputs == ["model_part_name"]
    assert "computing_model_part_name" not in others
    assert set(others) == {"variable_name", "value"}


# --- Integration tests against the real Kratos source tree ------------------

@needs_source
def test_get_process_defaults_real_process():
    info = process_catalog.get_process_defaults("assign_scalar_variable_process")
    assert info is not None
    assert "variable_name" in info["default_settings"]
    assert "model_part_name" in info["input_model_parts"]
    assert info["param_types"]["variable_name"] == "string"


@needs_source
def test_get_process_defaults_unknown_module():
    assert process_catalog.get_process_defaults("not_a_real_process") is None


@needs_source
def test_parse_rate_reasonable():
    # A meaningful share of the standard BC/load processes must parse.
    files = source_catalog.python_process_files()
    assert files
    parsed = sum(1 for _app, f in files
                 if process_catalog.get_process_defaults(f.stem) is not None)
    assert parsed >= len(files) * 0.4  # C++-validated/base processes legitimately fail
