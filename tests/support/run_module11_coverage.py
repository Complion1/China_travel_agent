"""Measure the Module 11 suite in this process and enforce D §16.3 / V §4.4."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import coverage
import pytest


SOURCE_PACKAGE = "app.durable_execution"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "app" / "durable_execution"
TEST_TARGETS = (
    "tests/unit/durable_execution",
    "tests/contract/durable_execution",
    "tests/integration/durable_execution",
)
THRESHOLD_NUMERATOR = 7
THRESHOLD_DENOMINATOR = 10


def pytest_arguments() -> list[str]:
    return ["-q", "-p", "no:cacheprovider", "--import-mode=importlib", *TEST_TARGETS]


def new_coverage(data_file: Path) -> coverage.Coverage:
    # A directory source avoids importing the package before start().  Ignoring
    # pyproject.toml also prevents its Module 02 source and 95% gate leaking in.
    measured = coverage.Coverage(
        data_file=str(data_file),
        config_file=False,
        source=[str(SOURCE_DIR)],
        branch=True,
    )
    # Alembic's migration directories are namespace directories without
    # __init__.py; otherwise coverage silently leaves them out of the total.
    measured.set_option("report:include_namespace_packages", True)
    return measured


class PytestMetrics:
    def __init__(self) -> None:
        self.collected = 0
        self.collection_errors = 0
        self._statuses: dict[str, list[str]] = {}
        self._rerun_reports = 0

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors += 1

    def pytest_runtest_logreport(self, report: Any) -> None:
        statuses = self._statuses.setdefault(report.nodeid, [])
        if report.outcome == "rerun":
            self._rerun_reports += 1
            statuses.append("rerun")
        elif report.when in {"setup", "call", "teardown"}:
            if report.failed:
                statuses.append("failed")
            elif report.skipped:
                statuses.append("xfail" if getattr(report, "wasxfail", False) else "skipped")
            elif report.when == "call" and report.passed:
                statuses.append("xfail" if getattr(report, "wasxfail", False) else "passed")

    def result_counts(self) -> dict[str, int]:
        counts = {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0, "rerun": self._rerun_reports}
        for statuses in self._statuses.values():
            for status in ("failed", "xfail", "skipped", "passed"):
                if status in statuses:
                    counts[status] += 1
                    break
        return counts


def coverage_summary(totals: dict[str, Any]) -> dict[str, int | float]:
    names = (
        "num_statements", "covered_lines", "missing_lines",
        "num_branches", "covered_branches", "missing_branches",
    )
    values = {name: totals[name] for name in names}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("invalid coverage totals")
    statements = values["num_statements"]
    branches = values["num_branches"]
    covered_lines = values["covered_lines"]
    covered_branches = values["covered_branches"]
    if (covered_lines + values["missing_lines"] != statements
            or covered_branches + values["missing_branches"] != branches):
        raise ValueError("inconsistent coverage totals")
    combined_total = statements + branches
    combined_covered = covered_lines + covered_branches

    def percent(covered: int, total: int) -> float | None:
        return 100 * covered / total if total else None

    return {
        "num_statements": statements,
        "covered_lines": covered_lines,
        "missing_lines": values["missing_lines"],
        "num_branches": branches,
        "covered_branches": covered_branches,
        "missing_branches": values["missing_branches"],
        "combined_numerator": combined_covered,
        "combined_denominator": combined_total,
        "line_percent": percent(covered_lines, statements),
        "branch_percent": percent(covered_branches, branches),
        "combined_percent": percent(combined_covered, combined_total),
    }


def evaluate(
    *,
    pytest_exit_code: int | None,
    collected: int,
    collection_errors: int,
    counts: dict[str, int],
    totals: dict[str, Any] | None,
    coverage_data_present: bool,
    coverage_json_present: bool,
    missing_source_files: list[str],
    harness_errors: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    if pytest_exit_code != 0:
        reasons.append("pytest_nonzero_or_exception")
    if collected <= 0:
        reasons.append("collected_zero")
    if collection_errors:
        reasons.append("collection_errors")
    for status in ("failed", "skipped", "xfail", "rerun"):
        if counts[status]:
            reasons.append(status)
    if counts["passed"] != collected:
        reasons.append("incomplete_execution")
    if not coverage_data_present:
        reasons.append("coverage_data_missing")
    if not coverage_json_present:
        reasons.append("coverage_json_missing")
    if missing_source_files:
        reasons.append("source_files_missing")
    if harness_errors:
        reasons.append("harness_error")

    summary: dict[str, Any] = {}
    if totals is None:
        reasons.append("coverage_totals_missing")
    else:
        try:
            summary = coverage_summary(totals)
        except (KeyError, TypeError, ValueError):
            reasons.append("coverage_totals_invalid")
        else:
            for label, numerator, denominator in (
                ("line", summary["covered_lines"], summary["num_statements"]),
                ("branch", summary["covered_branches"], summary["num_branches"]),
                ("combined", summary["combined_numerator"], summary["combined_denominator"]),
            ):
                if denominator == 0:
                    reasons.append(f"{label}_zero_denominator")
                elif numerator * THRESHOLD_DENOMINATOR < denominator * THRESHOLD_NUMERATOR:
                    reasons.append(f"{label}_below_threshold")

    return {
        "pytest_exit_code": pytest_exit_code,
        "collected": collected,
        "collection_errors": collection_errors,
        **counts,
        **summary,
        "coverage_data_present": coverage_data_present,
        "coverage_json_present": coverage_json_present,
        "missing_source_files": missing_source_files,
        "harness_errors": harness_errors,
        "failure_reasons": reasons,
        "gate_passed": not reasons,
        "harness_exit_code": 0 if not reasons else 1,
    }


def _source_files() -> set[str]:
    return {path.relative_to(PROJECT_ROOT).as_posix() for path in SOURCE_DIR.rglob("*.py")}


def run(output_dir: Path) -> int:
    if not output_dir.is_absolute() or output_dir.resolve().is_relative_to(PROJECT_ROOT):
        raise ValueError("output directory must be absolute and outside the project")
    output_dir.mkdir(parents=False, exist_ok=False)
    data_file = output_dir / ".coverage"
    json_file = output_dir / "coverage.json"
    summary_file = output_dir / "summary.json"
    metrics = PytestMetrics()
    measured = new_coverage(data_file)
    pytest_exit_code: int | None = None
    errors: list[str] = []
    started = False
    try:
        measured.start()
        started = True
        try:
            pytest_exit_code = int(pytest.main(pytest_arguments(), plugins=[metrics]))
        except BaseException as exc:
            errors.append(f"pytest:{type(exc).__name__}")
    except BaseException as exc:
        errors.append(f"coverage_start:{type(exc).__name__}")
    finally:
        if started:
            try:
                measured.stop()
                measured.save()
            except BaseException as exc:
                errors.append(f"coverage_save:{type(exc).__name__}")

    totals: dict[str, Any] | None = None
    reported_files: set[str] = set()
    if data_file.is_file():
        try:
            measured.json_report(outfile=str(json_file), pretty_print=False)
            report = json.loads(json_file.read_text(encoding="utf-8"))
            totals = report["totals"]
            reported_files = set(report["files"])
        except BaseException as exc:
            errors.append(f"coverage_report:{type(exc).__name__}")

    expected_files = _source_files()
    result = evaluate(
        pytest_exit_code=pytest_exit_code,
        collected=metrics.collected,
        collection_errors=metrics.collection_errors,
        counts=metrics.result_counts(),
        totals=totals,
        coverage_data_present=data_file.is_file(),
        coverage_json_present=json_file.is_file(),
        missing_source_files=sorted(expected_files - reported_files),
        harness_errors=errors,
    )
    result.update({
        "source": SOURCE_PACKAGE,
        "source_directory": str(SOURCE_DIR),
        "config_file": False,
        "branch": True,
        "threshold_percent": 70,
        "collection_scope": "this Python process only; spawned workers are excluded",
        "collector_pid": os.getpid(),
        "test_targets": list(TEST_TARGETS),
        "expected_source_file_count": len(expected_files),
        "reported_source_file_count": len(reported_files),
        "output_dir": str(output_dir),
        "coverage_data_file": str(data_file),
        "coverage_json_file": str(json_file),
        "summary_file": str(summary_file),
    })
    summary_file.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return result["harness_exit_code"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        return run(args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(f"invalid output location: {type(exc).__name__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
