"""生产 import 只能依赖标准库、Pydantic 和三个上游包根。"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION = tuple((ROOT / "app" / "hotel_selection").glob("*.py"))
ALLOWED_ROOTS = {
    "app.requirements",
    "app.workflow_contracts",
    "app.baidu_map",
    "pydantic",
}
FORBIDDEN_MARKERS = (
    "app.llm_runtime",
    "app.requirement_subgraph",
    "langchain",
    "langgraph",
    "mcp",
    "httpx",
    "requests",
    "secret",
)


def test_production_imports_use_only_approved_package_roots() -> None:
    for path in PRODUCTION:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("app."):
                    assert node.module in ALLOWED_ROOTS or node.module.startswith("app.hotel_selection")
                elif node.module.split(".", 1)[0] not in {"typing", "enum", "datetime", "hashlib", "json", "re", "__future__"}:
                    assert node.module.startswith("pydantic")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", 1)[0] in {
                        "hashlib",
                        "json",
                        "re",
                    }


def test_forbidden_runtime_markers_are_absent_from_production_sources() -> None:
    for path in PRODUCTION:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.lower())
            elif isinstance(node, ast.Import):
                imported.extend(alias.name.lower() for alias in node.names)
        assert not any(marker in module for module in imported for marker in FORBIDDEN_MARKERS)
