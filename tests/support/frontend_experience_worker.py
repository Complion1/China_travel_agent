"""Docker-only M24 verification with explicit resources and whole-package coverage."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import coverage
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support.run_module14_coverage import PytestMetrics, test_receipt

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app" / "frontend_experience"


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def identity() -> None:
    prefix = os.environ.get("MODULE24_TEST_PREFIX", "")
    old_prefix = os.environ.get("MODULE14_TEST_PREFIX", "")
    if (re.fullmatch(r"cta-m24-[a-z0-9-]{5,14}", prefix) is None
            or old_prefix != "cta-m14-m24-" + prefix.removeprefix("cta-m24-")
            or os.environ.get("MODULE24_TEST_IDENTITY_TOKEN") != prefix + "-identity"
            or os.environ.get("MODULE14_TEST_IDENTITY_TOKEN") != old_prefix + "-identity"
            or os.environ.get("POSTGRES_HOST") != "postgres"
            or os.environ.get("POSTGRES_PORT") != "5432"
            or os.environ.get("POSTGRES_USER") != "cta_m24_test"):
        raise RuntimeError("isolated M24 identity required")
    database = os.environ.get("POSTGRES_DB", "")
    if prefix.replace("-", "_") not in database or old_prefix.replace("-", "_") not in database:
        raise RuntimeError("isolated M24 database required")


def initialize(output: Path) -> None:
    if coverage.__version__ != "7.15.1":
        raise RuntimeError("coverage version differs from lock")
    if output.exists():
        raise RuntimeError("fresh coverage directory required")
    output.mkdir()
    # Runtime and browser images use different users but share test receipts.
    output.chmod(0o777)
    (output / "coverage.ini").write_text(
        f"[run]\nsource = {SOURCE}\nbranch = True\nparallel = True\npatch = subprocess\n"
        f"data_file = {output / '.coverage'}\n[report]\ninclude_namespace_packages = True\nfail_under = 0\n",
        encoding="utf-8")
    write(output / "protocol.json", dict(source="app.frontend_experience", branch=True,
        patch="subprocess", version=coverage.__version__, threshold=70,
        files=sorted(path.relative_to(ROOT).as_posix() for path in SOURCE.rglob("*.py"))))


def checked(arguments: list[str]) -> None:
    subprocess.run([sys.executable, *arguments], check=True, timeout=120)


def prepare(output: Path) -> None:
    identity()
    checked(["-m", "alembic", "-c", "app/artifact_persistence/alembic.ini", "upgrade", "head"])
    checked(["-m", "app.durable_execution.migrate"])
    checked(["-m", "app.web_application.migrate"])
    checked(["-m", "coverage", "run", "--rcfile", str(output / "coverage.ini"),
        "-m", "app.frontend_experience.migrate"])
    checked(["-m", "coverage", "run", "--rcfile", str(output / "coverage.ini"),
        "-m", "app.frontend_experience.migrate", "--check"])


def suite(mode: str, output: Path) -> int:
    identity()
    if mode == "module":
        targets = ["tests/unit/frontend_experience", "tests/contract/frontend_experience",
            "tests/unit/web_application", "tests/contract/web_application"]
    elif mode == "module-integration":
        targets = ["tests/integration/frontend_experience", "tests/integration/web_application"]
    elif mode == "all-unit":
        targets = ["tests/unit", "tests/contract"]
    else:
        targets = ["tests/browser/web_application"]
    metrics = PytestMetrics()
    measured = coverage.Coverage(config_file=str(output / "coverage.ini")) if mode in {"module", "module-integration"} else None
    if measured is not None:
        measured.start()
    try:
        code = int(pytest.main(["-q" if mode == "all-unit" else "-v", "--tb=short", "--maxfail=1", "--import-mode=importlib",
            "--override-ini", "addopts=", "-p", "no:cacheprovider", *targets], plugins=[metrics]))
    finally:
        if measured is not None:
            measured.stop()
            measured.save()
    receipt = test_receipt(metrics, code)
    write(output / f"{mode}-tests.json", receipt)
    print(json.dumps(receipt), flush=True)
    return receipt["harness_exit_code"]


def report(output: Path) -> int:
    measured = coverage.Coverage(config_file=str(output / "coverage.ini"), data_suffix=False)
    measured.combine(strict=True, keep=True)
    measured.save()
    path = output / "coverage.json"
    measured.json_report(outfile=str(path))
    result = json.loads(path.read_text(encoding="utf-8"))
    totals = result["totals"]
    line = dict(covered=totals["covered_lines"], total=totals["num_statements"])
    branch = dict(covered=totals["covered_branches"], total=totals["num_branches"])
    combined = dict(covered=line["covered"] + branch["covered"], total=line["total"] + branch["total"])
    for item in (line, branch, combined):
        item["percent"] = 100 * item["covered"] / item["total"] if item["total"] else 100
    expected = {path.relative_to(ROOT).as_posix() for path in SOURCE.rglob("*.py")}
    present = {Path(name).relative_to(ROOT).as_posix() if Path(name).is_absolute() else name for name in result["files"]}
    tests = [json.loads((output / f"{mode}-tests.json").read_text())
        for mode in ("module", "module-integration")]
    receipt = dict(source="app.frontend_experience", line=line, branch=branch, combined=combined,
        tests=tests, missing_files=sorted(expected - present), gate_passed=not (expected - present)
            and all(test["gate_passed"] for test in tests) and coverage.__version__ == "7.15.1"
            and min(line["percent"], branch["percent"], combined["percent"]) >= 70)
    write(output / "coverage-receipt.json", receipt)
    measured.report(show_missing=True)
    print(json.dumps(receipt), flush=True)
    return 0 if receipt["gate_passed"] else 1


def ready(output: Path) -> None:
    for _ in range(120):
        try:
            value = json.loads((output.parent / "browser-origin.json").read_text())
            assert value["offline"] is True
            with urllib.request.urlopen(value["origin"] + "/__test/info", timeout=1) as response:
                assert json.load(response)["provider_calls"] == 0
            return
        except (OSError, ValueError, AssertionError):
            time.sleep(.25)
    raise RuntimeError("fresh offline browser server not ready")


def image_manifest(path: Path) -> None:
    expected = json.loads(path.read_text(encoding="utf-8-sig"))
    for name, digest in expected["files"].items():
        if name.startswith("app/"):
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    print("M24_CURRENT_APPLICATION_FILES_VERIFIED")


def serve(preview_origin: str | None) -> None:
    identity()
    from tests.support import module14_browser_worker as worker
    if preview_origin is not None:
        if re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{3,4}", preview_origin) is None:
            raise RuntimeError("loopback preview origin required")
        original = worker.web_settings
        def settings(**kwargs):
            return original(**(kwargs | {"public_origin":preview_origin}))
        worker.web_settings = settings
    asyncio.run(worker.serve(8080))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["init", "prepare", "module", "all-unit",
        "module-integration", "browser", "report", "ready", "manifest", "compile", "serve"])
    parser.add_argument("--output", type=Path, default=Path("/evidence/m24-coverage"))
    parser.add_argument("--preview-origin")
    args = parser.parse_args()
    output = args.output
    if not output.is_absolute() or not output.is_relative_to(Path("/evidence")):
        raise RuntimeError("explicit evidence mount required")
    if args.mode == "init":
        initialize(output)
    elif args.mode == "prepare":
        prepare(output)
    elif args.mode in {"module", "module-integration", "all-unit", "browser"}:
        return suite(args.mode, output)
    elif args.mode == "report":
        return report(output)
    elif args.mode == "ready":
        ready(output)
    elif args.mode == "manifest":
        image_manifest(output)
    elif args.mode == "compile":
        import compileall
        destination = Path("/tmp/m24-compile")
        shutil.copytree(ROOT / "app", destination)
        assert compileall.compile_dir(destination, quiet=1)
    else:
        serve(args.preview_origin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
