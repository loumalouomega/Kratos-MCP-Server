from __future__ import annotations

import pytest

from kratos_mcp import kratos_env


def kratos_available() -> bool:
    return kratos_env.is_available()


def pytest_collection_modifyitems(config, items):
    if kratos_available():
        return
    skip = pytest.mark.skip(reason="Kratos build not available (set KRATOS_ROOT)")
    for item in items:
        if "kratos" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def case_dir(tmp_path):
    return tmp_path / "case"
