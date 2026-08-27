import importlib.metadata

import tca
from tca.mcp import SERVER_INFO


def test_package_cli_and_mcp_versions_match() -> None:
    installed = importlib.metadata.version("technocore-contribution-agent")
    assert installed == "0.2.0"
    assert tca.__version__ == installed
    assert SERVER_INFO["version"] == installed
