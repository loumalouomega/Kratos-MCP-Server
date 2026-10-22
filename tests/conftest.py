from __future__ import annotations

import os

import pytest

from kratos_mcp import kratos_env


def kratos_available() -> bool:
    return kratos_env.is_available()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("KRATOS_MCP_TEST_NETWORK") != "1":
        skip_net = pytest.mark.skip(reason="network test (set KRATOS_MCP_TEST_NETWORK=1)")
        for item in items:
            if "network" in item.keywords:
                item.add_marker(skip_net)
    if kratos_available():
        return
    skip = pytest.mark.skip(reason="Kratos build not available (set KRATOS_ROOT)")
    for item in items:
        if "kratos" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def case_dir(tmp_path):
    return tmp_path / "case"
