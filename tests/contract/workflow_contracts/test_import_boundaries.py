"""Static import-boundary checks for the module 04 production package."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[3] / "app" / "workflow_contracts"
ALLOWED_EXTERNAL_APP_IMPORT = ("app.requirements", ("RequirementDomainOutcome",))
FORBIDDEN_IMPORT_TOKENS = {
    "app.llm_runtime",
    "app.requirement_extraction",
    "app.graph",
    "app.persistence",
    "app.agent",
    "app.tools",
    "app.config",
    "langchain",
    "langgraph",
    "fastapi",
    "asyncpg",
    "psycopg",
    "mcp",
    "deepseek",
    "baidu",
    "requests",
    "httpx",
    "socket",
}
FORBIDDEN_RUNTIME_TOKENS = {
    "subprocess",
    "os.getenv",
    "os.environ",
    "socket",
    "requests",
    "httpx",
    "open(",
    "asyncio.run",
    "create_task",
}


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name
    return "." * node.level + (node.module or "")


def _top_level_module(name: str) -> str:
    return name.lstrip(".").split(".", 1)[0]


def test_production_imports_are_within_the_allowlist() -> None:
    assert PACKAGE_ROOT.is_dir()
    source_files = tuple(sorted(PACKAGE_ROOT.rglob("*.py")))
    assert source_files

    stdlib = set(sys.stdlib_module_names)
    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        source_text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_RUNTIME_TOKENS:
            assert token not in source_text, f"forbidden runtime token {token}: {path}"

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if any(alias.name == "*" for alias in node.names):
                raise AssertionError(f"wildcard import: {path}:{node.lineno}")

            if isinstance(node, ast.ImportFrom) and node.level:
                if node.module and any(
                    segment.startswith("_")
                    for segment in node.module.split(".")
                    if segment
                ):
                    raise AssertionError(f"private relative module import: {path}:{node.lineno}")
                continue

            module = _module_name(node)
            if module == "app.requirements":
                assert isinstance(node, ast.ImportFrom)
                assert tuple(alias.name for alias in node.names) == (
                    "RequirementDomainOutcome",
                )
                continue
            if module.startswith("app."):
                assert module == "app.workflow_contracts" or module.startswith(
                    "app.workflow_contracts."
                ), f"forbidden app import {module}: {path}:{node.lineno}"
                assert not any(
                    segment.startswith("_")
                    for segment in module.split(".")[2:]
                ), f"private production module import {module}: {path}:{node.lineno}"
                continue

            top_level = _top_level_module(module)
            assert top_level in stdlib or top_level == "pydantic", (
                f"non-stdlib import {module}: {path}:{node.lineno}"
            )
            assert not any(
                token in module.lower() for token in FORBIDDEN_IMPORT_TOKENS
            ), f"forbidden dependency import {module}: {path}:{node.lineno}"


def test_subpackage_init_files_are_docstring_only() -> None:
    for relative_path in (
        "artifacts/__init__.py",
        "budgets/__init__.py",
        "events/__init__.py",
    ):
        path = PACKAGE_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert len(tree.body) == 1
        statement = tree.body[0]
        assert isinstance(statement, ast.Expr)
        assert isinstance(statement.value, ast.Constant)
        assert isinstance(statement.value.value, str)


def test_production_has_no_cyclic_package_root_entry_or_private_module_entry() -> None:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path == PACKAGE_ROOT / "__init__.py":
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                assert module != "app.workflow_contracts"
                assert not module.startswith("app.workflow_contracts._")
