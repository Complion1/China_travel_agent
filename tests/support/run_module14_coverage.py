"""E14 W08/W10: explicit Docker-only suites and whole-package coverage."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import coverage
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support.run_module11_coverage import PytestMetrics, coverage_summary, evaluate

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app" / "web_application"
TARGETS = {
    "unit": ["tests/unit/web_application", "tests/contract/web_application",
        "tests/unit/artifact_persistence", "tests/contract/artifact_persistence",
        "tests/unit/demo_workflow", "tests/contract/demo_workflow"],
    "integration": ["tests/integration/web_application",
        "tests/integration/artifact_persistence/test_capacity_snapshot.py"],
    "all-unit": ["tests/unit", "tests/contract"],
    "m11": ["tests/integration/durable_execution"],
    "m12-unit": ["tests/unit/result_formatting", "tests/contract/result_formatting"],
    "m12-integration": ["tests/integration/result_formatting"],
}


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def test_receipt(metrics: PytestMetrics, exit_code: int) -> dict:
    counts = metrics.result_counts()
    valid = (exit_code == 0 and metrics.collected > 0 and metrics.collection_errors == 0
        and counts["passed"] == metrics.collected
        and all(counts[name] == 0 for name in ("failed", "skipped", "xfail", "rerun")))
    return dict(pytest_exit_code=exit_code, collected=metrics.collected,
        collection_errors=metrics.collection_errors, **counts,
        gate_passed=valid, harness_exit_code=0 if valid else 1)


def initialize(output: Path) -> int:
    if output.exists():
        raise ValueError("coverage output already exists")
    if coverage.__version__ != "7.15.1":
        raise ValueError("coverage version differs from lock")
    output.mkdir()
    (output / "coverage.ini").write_text(
        f"[run]\nsource = {SOURCE}\nbranch = True\nparallel = True\n"
        f"patch = subprocess\ndata_file = {output / '.coverage'}\n"
        "[report]\ninclude_namespace_packages = True\n", encoding="utf-8")
    write(output / "protocol.json", dict(version=coverage.__version__, branch=True,
        patch="subprocess", source=str(SOURCE), source_files=sorted(
            path.relative_to(ROOT).as_posix() for path in SOURCE.rglob("*.py"))))
    print("M14_COVERAGE_INITIALIZED subprocess branch whole-package", flush=True)
    return 0


def run_suite(mode: str, output: Path) -> int:
    measured = None
    if mode in {"unit", "integration"}:
        measured = coverage.Coverage(config_file=str(output / "coverage.ini"))
    elif mode == "m12-unit":
        measured = coverage.Coverage(config_file=False,
            data_file=str(output / ".coverage-m12"),
            source=[str(ROOT / "app" / "result_formatting")], branch=True)
        measured.set_option("report:include_namespace_packages", True)
    metrics = PytestMetrics()
    if measured is not None:
        measured.start()
    try:
        result = int(pytest.main(["-q", "--tb=short", "--maxfail=1",
            "--import-mode=importlib", "-p", "no:cacheprovider", *TARGETS[mode]], plugins=[metrics]))
    finally:
        if measured is not None:
            measured.stop()
            measured.save()
    receipt = test_receipt(metrics, result)
    if mode == "m12-unit":
        path = output / "m12-coverage.json"
        measured.json_report(outfile=str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        receipt["coverage"] = coverage_summary(raw["totals"])
        receipt["coverage_threshold"] = "DIAGNOSTIC_NO_NUMERIC_GATE_D12_10_V12_6_2"
        measured.report(show_missing=True)
    write(output / f"{mode}-tests.json", receipt)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return receipt["harness_exit_code"]


def report(output: Path) -> int:
    measured = coverage.Coverage(config_file=str(output / "coverage.ini"), data_suffix=False)
    fragments = sorted(path.name for path in output.glob(".coverage.*"))
    measured.combine(strict=True, keep=True)
    measured.save()
    path = output / "coverage.json"
    measured.json_report(outfile=str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    receipts = [json.loads((output / f"{mode}-tests.json").read_text(encoding="utf-8"))
        for mode in ("unit", "integration")]
    counts = {name: sum(item[name] for item in receipts)
        for name in ("passed", "failed", "skipped", "xfail", "rerun")}
    expected = {path.relative_to(ROOT).as_posix() for path in SOURCE.rglob("*.py")}
    files = {Path(name).relative_to(ROOT).as_posix() if Path(name).is_absolute() else name:
        record for name, record in raw["files"].items()}
    # Startup, explicit migration, Key generation and actual namespace migration
    # must be measured, including their real child interpreters, not just listed.
    required = ("__main__.py", "assembly.py", "store.py", "keygen.py", "migrate.py",
        "migrations/env.py", "migrations/versions/m14_0001_web_application.py")
    errors = ["required_source_not_executed:" + name for name in required
        if not files.get("app/web_application/" + name, {}).get("executed_lines")]
    if not fragments or coverage.__version__ != "7.15.1":
        errors.append("coverage_protocol_invalid")
    summary = evaluate(pytest_exit_code=0 if all(item["gate_passed"] for item in receipts) else 1,
        collected=sum(item["collected"] for item in receipts),
        collection_errors=sum(item["collection_errors"] for item in receipts), counts=counts,
        totals=raw["totals"], coverage_data_present=(output / ".coverage").is_file(),
        coverage_json_present=path.is_file(), missing_source_files=sorted(expected - files.keys()),
        harness_errors=errors)
    summary.update(source="app.web_application", branch=True, patch="subprocess",
        threshold_percent=70, fragment_count=len(fragments), source_files=sorted(files),
        required_source_executed={name: bool(files.get("app/web_application/"+name, {}).get("executed_lines"))
            for name in required})
    write(output / "summary.json", summary)
    measured.report(show_missing=True)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary["harness_exit_code"]


def initialize_upstreams(output: Path) -> int:
    """Create only fresh named DBs in this harness's already verified PG server."""
    import psycopg
    from psycopg import sql
    prefix = os.environ.get("MODULE14_TEST_PREFIX", "")
    token = os.environ.get("MODULE14_TEST_IDENTITY_TOKEN", "")
    base_db = prefix.replace("-", "_") + "_db"
    if (re.fullmatch(r"cta-m14-[a-z0-9-]{8,40}", prefix) is None or token != prefix+"-identity"
            or os.environ.get("POSTGRES_DB") != base_db
            or os.environ.get("POSTGRES_HOST") != "m14-test-postgres"
            or os.environ.get("POSTGRES_USER") != "cta_m14_test"):
        raise ValueError("upstream isolated owner identity invalid")
    digest = hashlib.sha256(prefix.encode()).hexdigest()[:8]
    short = prefix if len(prefix) <= 35 else prefix[:26]+"-"+digest
    m12_prefix = "china-travel-agent-m12-"+prefix.removeprefix("cta-m14-")[:18]+"-"+digest
    roles = ("main", "migration", "capacity", "scope", "noauto", "fail")
    identity = dict(owner_project=prefix,
        m10=dict(prefix=short, database=short+"-identity-ap", capacity_database=short+"-identity-ap_cap"),
        m11=dict(prefix=short+"-m11", databases={role:short+"-m11-identity-"+role for role in roles}),
        m13=dict(prefix=short+"-m13", databases={role:short+"-m13-identity-"+role for role in roles}),
        m12=dict(prefix=m12_prefix, database=m12_prefix.replace("-", "_")+"_main", user="m12_test"))
    databases = [identity["m10"]["database"], identity["m10"]["capacity_database"],
        *identity["m11"]["databases"].values(), *identity["m13"]["databases"].values(), identity["m12"]["database"]]
    if len(set(databases)) != len(databases) or any(len(name.encode()) > 63 for name in databases):
        raise ValueError("upstream database identity collision")
    with psycopg.connect(host=os.environ["POSTGRES_HOST"], port=5432, dbname=base_db,
            user="cta_m14_test", password=os.environ["POSTGRES_PASSWORD"], autocommit=True) as connection:
        if connection.execute("SELECT current_database(), current_user").fetchone() != (base_db, "cta_m14_test"):
            raise ValueError("upstream reached wrong server")
        if connection.execute("SELECT datname FROM pg_database WHERE datname = ANY(%s)", (databases,)).fetchall():
            raise ValueError("upstream databases already exist")
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname='m12_test'").fetchone():
            raise ValueError("upstream role already exists")
        connection.execute(sql.SQL("CREATE ROLE m12_test LOGIN PASSWORD {} ").format(sql.Literal(os.environ["POSTGRES_PASSWORD"])))
        for database in databases:
            owner = "m12_test" if database == identity["m12"]["database"] else "cta_m14_test"
            connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(owner)))
    write(output / "upstream-identities.json", identity)
    print("M14_UPSTREAM_IDENTITIES_CREATED databases=15 independent=true", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["init", "report", "init-upstream", *TARGETS])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute() or not args.output.is_relative_to(Path("/evidence")):
        raise ValueError("explicit evidence mount required")
    if args.mode == "init":
        return initialize(args.output)
    if args.mode == "report":
        return report(args.output)
    if args.mode == "init-upstream":
        return initialize_upstreams(args.output)
    return run_suite(args.mode, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
