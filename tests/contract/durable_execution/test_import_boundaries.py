from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[3] / "app" / "durable_execution"
FORBIDDEN_MODULES = (
    "app.requirements",
    "app.hotel_selection",
    "app.candidate_research",
    "app.itinerary_planning",
    "requests",
    "httpx",
    "importlib",
    "pickle",
)
ALLOWED_APP_ROOTS = (
    "app.durable_execution",
    "app.requirement_subgraph",
    "app.workflow_contracts",
)


def _module_is_or_is_child(module: str, root: str) -> bool:
    return module == root or module.startswith(root + ".")


def test_module11_does_not_directly_import_business_or_dynamic_boundaries() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
                assert not any(
                    _module_is_or_is_child(module, forbidden)
                    for module in imported
                    for forbidden in FORBIDDEN_MODULES
                ), f"{path}:{node.lineno}: forbidden import"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not any(
                    _module_is_or_is_child(module, forbidden)
                    for forbidden in FORBIDDEN_MODULES
                ), f"{path}:{node.lineno}: forbidden from-import"
                if module.startswith("app."):
                    assert any(
                        _module_is_or_is_child(module, allowed)
                        for allowed in ALLOWED_APP_ROOTS
                    ), f"{path}:{node.lineno}: unapproved app import {module}"
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in {"__import__", "eval", "exec"}, (
                        f"{path}:{node.lineno}: dynamic import/evaluation"
                    )
                if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                    raise AssertionError(f"{path}:{node.lineno}: dynamic import")
