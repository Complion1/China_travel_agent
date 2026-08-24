from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[3]


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_application_consumers_do_not_cross_private_or_model_boundaries() -> None:
    for path in (ROOT / "app").rglob("*.py"):
        if (ROOT / "app" / "baidu_map") in path.parents:
            continue
        imports = tuple(_imports(path))
        assert not any(name.startswith("app.baidu_map._") for name in imports), path
        assert not any(name.startswith("app.baidu_map.models") for name in imports), path
        assert not any(name == "mcp" or name.startswith("mcp.") for name in imports), path


def test_public_facade_does_not_export_sdk_gateway_or_raw_response_types() -> None:
    import app.baidu_map as facade

    forbidden = {
        "BaiduMapMcpGateway", "ClientSession", "CallToolResult", "ToolRegistry",
        "_OFFICIAL_ENDPOINT", "session", "endpoint", "parser", "registry", "runtime",
    }
    assert forbidden.isdisjoint(facade.__all__)
    assert all(not hasattr(facade, name) for name in forbidden)


def test_module06_imports_only_utc_clock_from_module04_public_root() -> None:
    for path in (ROOT / "app" / "baidu_map").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "app.workflow_contracts." not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module == "app.workflow_contracts":
                assert {alias.name for alias in node.names} == {"UtcClock"}
