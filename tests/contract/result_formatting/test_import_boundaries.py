"""RF-API: public upstream dependencies only, with no import-time I/O."""

import ast
import os
from pathlib import Path
import subprocess
import sys


PUBLIC_UPSTREAM = {
    "app.workflow_contracts", "app.requirements", "app.hotel_selection",
    "app.candidate_research", "app.itinerary_planning",
}


def test_imports_use_only_public_upstream_facades_and_no_provider_clients():
    for path in Path("app/result_formatting").glob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = (node.module,)
            elif isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            else:
                continue
            for name in names:
                assert name is not None
                if name.startswith("app."):
                    assert name in PUBLIC_UPSTREAM, (path, name)
                else:
                    assert name.split(".")[0] in sys.stdlib_module_names | {"pydantic"}, (path, name)
                assert name.split(".")[0] not in {"os", "pathlib", "socket", "subprocess", "time"}, (path, name)
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            assert called not in {"open", "eval", "exec", "__import__", "datetime.now", "datetime.utcnow",
                                  "date.today", "importlib.import_module"}, (path, called)


def test_fresh_import_performs_no_file_write_network_or_process_launch():
    # This child runs only inside the same Docker pytest execution. Reading
    # Python modules is allowed; importing must not create resources or files.
    script = r'''
import os
import sys
def audit(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(mark in mode for mark in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            raise AssertionError("import performed a file write")
    if event.startswith(("socket.", "subprocess.")) or event in {"os.system", "os.mkdir", "os.remove", "os.rename"}:
        raise AssertionError("import performed external I/O")
sys.addaudithook(audit)
import app.result_formatting
assert app.result_formatting.ResultFormattingService
'''
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True,
                            text=True, check=False, env=environment, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
