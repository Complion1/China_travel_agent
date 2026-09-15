"""Run Module 10 coverage in one Python process and enforce the AP-T06 gate."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import coverage
import pytest


SOURCE_PACKAGE = "app.artifact_persistence"
LINE_BRANCH_THRESHOLD = 70.0
_RESTART_SEED = (
    "tests/integration/artifact_persistence/test_restart_persistence.py::test_restart_seed"
)
_RESTART_VERIFY = (
    "tests/integration/artifact_persistence/test_restart_persistence.py::test_restart_verify"
)


def percentage(covered: int, total: int) -> float:
    """Return a safe percentage; an empty denominator is a vacuous 100%."""

    if total == 0:
        return 100.0
    return covered * 100.0 / total


def coverage_summary(totals: dict[str, int]) -> dict[str, float | int]:
    """Calculate raw line, branch, and combined coverage from Coverage JSON totals."""

    statements = totals["num_statements"]
    covered_lines = totals["covered_lines"]
    branches = totals["num_branches"]
    covered_branches = totals["covered_branches"]
    return {
        "statements": statements,
        "covered_lines": covered_lines,
        "missing_lines": totals["missing_lines"],
        "branches": branches,
        "covered_branches": covered_branches,
        "missing_branches": totals["missing_branches"],
        "line_percent": percentage(covered_lines, statements),
        "branch_percent": percentage(covered_branches, branches),
        "combined_percent": percentage(
            covered_lines + covered_branches,
            statements + branches,
        ),
    }


def coverage_gate(summary: dict[str, float | int]) -> bool:
    """Apply the fixed AP-T06 line, branch, and combined threshold."""

    return (
        summary["statements"] > 0
        and summary["line_percent"] >= LINE_BRANCH_THRESHOLD
        and summary["branch_percent"] >= LINE_BRANCH_THRESHOLD
        and summary["combined_percent"] >= LINE_BRANCH_THRESHOLD
    )


class _PytestMetrics:
    def __init__(self) -> None:
        self.collected = 0
        self.collection_errors = 0
        self._statuses: dict[str, list[str]] = {}

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors += 1

    def pytest_runtest_logreport(self, report: Any) -> None:
        node_statuses = self._statuses.setdefault(report.nodeid, [])
        if report.outcome == "rerun":
            node_statuses.append("rerun")
            return
        if report.when in {"setup", "call", "teardown"}:
            if report.outcome == "failed":
                node_statuses.append("failed")
            elif report.outcome == "skipped":
                node_statuses.append(
                    "xfail" if getattr(report, "wasxfail", False) else "skipped"
                )
            elif report.when == "call" and report.outcome == "passed":
                node_statuses.append(
                    "xpass" if getattr(report, "wasxfail", False) else "passed"
                )

    def result_counts(self) -> dict[str, int]:
        counts = {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "xfail": 0,
            "rerun": 0,
        }
        for statuses in self._statuses.values():
            if "rerun" in statuses:
                counts["rerun"] += 1
            if "failed" in statuses:
                counts["failed"] += 1
            elif "xfail" in statuses or "xpass" in statuses:
                counts["xfail"] += 1
            elif "skipped" in statuses:
                counts["skipped"] += 1
            elif "passed" in statuses:
                counts["passed"] += 1
        return counts


def pytest_arguments() -> list[str]:
    return [
        "-q",
        "-p",
        "no:cacheprovider",
        "--import-mode=importlib",
        "tests/unit/artifact_persistence",
        "tests/contract/artifact_persistence",
        "tests/integration/artifact_persistence",
        f"--deselect={_RESTART_SEED}",
        f"--deselect={_RESTART_VERIFY}",
    ]


def run() -> int:
    metrics = _PytestMetrics()
    with tempfile.TemporaryDirectory(prefix="module10-coverage-") as directory:
        data_file = Path(directory) / ".coverage"
        json_file = Path(directory) / "coverage.json"
        measured = coverage.Coverage(
            data_file=str(data_file),
            source=[SOURCE_PACKAGE],
            branch=True,
        )
        measured.start()
        pytest_exit = int(pytest.main(pytest_arguments(), plugins=[metrics]))
        measured.stop()
        measured.save()
        measured.json_report(outfile=str(json_file), pretty_print=False)
        report = json.loads(json_file.read_text(encoding="utf-8"))

    totals = report["totals"]
    summary = coverage_summary(totals)
    result = {
        "source": SOURCE_PACKAGE,
        "branch": True,
        "one_process": True,
        "pytest_exit_code": pytest_exit,
        "harness_exit_code": 0,
        "collected": metrics.collected,
        "collection_errors": metrics.collection_errors,
        **metrics.result_counts(),
        **summary,
        "gate_passed": (
            pytest_exit == 0
            and metrics.collection_errors == 0
            and metrics.collected > 0
            and metrics.result_counts()["failed"] == 0
            and metrics.result_counts()["skipped"] == 0
            and metrics.result_counts()["xfail"] == 0
            and metrics.result_counts()["rerun"] == 0
            and coverage_gate(summary)
        ),
    }
    result["harness_exit_code"] = 0 if result["gate_passed"] else 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result["harness_exit_code"]


if __name__ == "__main__":
    raise SystemExit(run())
