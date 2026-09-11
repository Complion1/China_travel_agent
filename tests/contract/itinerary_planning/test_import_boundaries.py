import ast
from pathlib import Path


ROOT = Path(__file__).parents[4] / "app" / "itinerary_planning"
ALLOWED = {"app.requirements", "app.llm_runtime", "app.workflow_contracts", "app.baidu_map", "app.hotel_selection", "app.candidate_research"}


def test_production_imports_use_only_public_upstream_facades() -> None:
    forbidden = {"mcp", "httpx", "requests", "asyncpg", "psycopg2", "app.tooling", "app.map_execution", "app.service"}
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
            else:
                continue
            assert not any(name == item or name.startswith(item + ".") for name in names for item in forbidden), path.name
            assert not any(name.startswith("app.") and name.split(".", 2)[:2] == ["app", "itinerary_planning"] and name != "app.itinerary_planning" for name in names), path.name
            assert all(not name.startswith("app.") or name in ALLOWED or name == "app.itinerary_planning" for name in names), (path.name, names)
