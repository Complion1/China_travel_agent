import ast
from pathlib import Path


def test_no_private_upstream_import_or_second_execution_engine():
    for path in Path("app/demo_workflow").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                assert len(node.module.split(".")) == 2 or node.module.startswith("app.demo_workflow."), path
            if isinstance(node, ast.Import):
                assert not any(item.name.startswith(("psycopg", "sqlalchemy", "httpx", "requests")) for item in node.names), path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "compile", path
