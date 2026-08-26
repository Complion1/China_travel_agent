"""在单个 Python 进程内执行 Module 07 测试并计算三项原始 coverage。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import coverage
import pytest


_SOURCE = "app.hotel_selection"
_JSON_PATH = Path("/tmp/module07-coverage.json")
_DEFAULT_DATA_PATH = Path("/tmp/module07.coverage")
_THRESHOLD = 95.0
_TEST_PATHS = ("tests/unit/hotel_selection", "tests/contract/hotel_selection")


class _PytestStats:
    """以 pytest plugin 记录一次 pytest.main 调用的收集与结果。"""

    def __init__(self) -> None:
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.xfail = 0
        self.rerun = 0

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when != "call":
            return
        if report.outcome == "rerun":
            self.rerun += 1
        elif getattr(report, "wasxfail", False):
            self.xfail += 1
        elif report.outcome == "passed":
            self.passed += 1
        elif report.outcome == "failed":
            self.failed += 1
        elif report.outcome == "skipped":
            self.skipped += 1


def _data_path() -> Path:
    value = Path(os.environ.get("COVERAGE_FILE", str(_DEFAULT_DATA_PATH)))
    if value.parent != Path("/tmp"):
        raise ValueError("COVERAGE_FILE must be under /tmp")
    return value


def _integer(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid coverage count: {field}")
    return value


def _coverage_counts(data: dict[str, Any]) -> tuple[int, int, int, int]:
    totals = data.get("totals")
    if type(totals) is not dict:
        raise ValueError("coverage JSON has no totals object")
    covered_lines = _integer(totals.get("covered_lines"), field="covered_lines")
    num_statements = _integer(totals.get("num_statements"), field="num_statements")
    covered_branches = _integer(totals.get("covered_branches"), field="covered_branches")
    num_branches = _integer(totals.get("num_branches"), field="num_branches")
    if num_statements == 0 or covered_lines > num_statements:
        raise ValueError("invalid line coverage counts")
    if covered_branches > num_branches:
        raise ValueError("invalid branch coverage counts")
    return covered_lines, num_statements, covered_branches, num_branches


def _percent(covered: int, total: int) -> float:
    if total == 0:
        return 100.0
    return 100.0 * covered / total


def _report(stats: _PytestStats, counts: tuple[int, int, int, int]) -> int:
    covered_lines, num_statements, covered_branches, num_branches = counts
    line = _percent(covered_lines, num_statements)
    branch = _percent(covered_branches, num_branches)
    combined_total = num_statements + num_branches
    if combined_total == 0:
        raise ValueError("combined coverage denominator is zero")
    combined = 100.0 * (covered_lines + covered_branches) / combined_total
    print(
        "MODULE07_COVERAGE "
        f"collected={stats.collected} passed={stats.passed} failed={stats.failed} "
        f"skipped={stats.skipped} xfail={stats.xfail} rerun={stats.rerun}"
    )
    print(
        "MODULE07_COVERAGE_COUNTS "
        f"covered_lines={covered_lines} num_statements={num_statements} "
        f"covered_branches={covered_branches} num_branches={num_branches}"
    )
    print(
        "MODULE07_COVERAGE_PERCENT "
        f"line={line:.2f} branch={branch:.2f} combined={combined:.2f}"
    )
    if stats.collected == 0:
        raise ValueError("Module 07 test collection is empty")
    if stats.passed != stats.collected or any(
        value != 0 for value in (stats.failed, stats.skipped, stats.xfail, stats.rerun)
    ):
        raise ValueError("Module 07 pytest result counts are not all passing")
    if min(line, branch, combined) < _THRESHOLD:
        raise ValueError("Module 07 coverage is below 95.00%")
    return 0


def main() -> int:
    stats = _PytestStats()
    data_path = _data_path()
    cov = coverage.Coverage(branch=True, source=[_SOURCE], data_file=str(data_path))
    cov.erase()
    result = 1
    try:
        cov.start()
        result = int(
            pytest.main(
                ["-q", "-p", "no:cacheprovider", *_TEST_PATHS],
                plugins=[stats],
            )
        )
    finally:
        cov.stop()
        cov.save()
    try:
        _JSON_PATH.unlink(missing_ok=True)
        cov.json_report(outfile=str(_JSON_PATH))
        with _JSON_PATH.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if type(payload) is not dict:
            raise ValueError("coverage JSON root is not an object")
        counts = _coverage_counts(payload)
        report_result = _report(stats, counts)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"MODULE07_COVERAGE_ERROR {error}", file=sys.stderr)
        return 1
    if result != 0:
        print(f"MODULE07_PYTEST_EXIT_CODE {result}", file=sys.stderr)
        return 1
    return report_result


if __name__ == "__main__":
    raise SystemExit(main())
