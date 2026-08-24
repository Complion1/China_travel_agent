"""冻结的模块 06 unit/contract 覆盖率门禁入口。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import json

import coverage
import pytest


_DATA_FILE = "/tmp/module06-coverage"
_JSON_FILE = "/tmp/module06-coverage.json"
_TEST_PATHS = ("tests/unit/baidu_map", "tests/contract/baidu_map")
_THRESHOLD = 95


def _percent(covered: int, total: int, *, zero_is_full: bool) -> str:
    if total == 0:
        value = Decimal("100" if zero_is_full else "0")
    else:
        value = (Decimal(covered * 100) / Decimal(total)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return f"{value:.2f}%"


def _passes(covered: int, total: int, *, zero_is_full: bool) -> bool:
    # Deliberately do not round before applying the gate.
    return (total == 0 and zero_is_full) or (total > 0 and covered * 100 >= total * _THRESHOLD)


def main() -> int:
    runner = coverage.Coverage(
        data_file=_DATA_FILE,
        branch=True,
        source=["app.baidu_map"],
    )
    runner.start()
    try:
        pytest_status = int(pytest.main(["-q", "-p", "no:cacheprovider", *_TEST_PATHS]))
    finally:
        runner.stop()
        runner.save()
    if pytest_status != 0:
        return pytest_status

    runner.json_report(outfile=_JSON_FILE)
    with open(_JSON_FILE, "r", encoding="utf-8") as stream:
        totals = json.load(stream)["totals"]
    line_covered = int(totals["covered_lines"])
    line_total = int(totals["num_statements"])
    branch_covered = int(totals["covered_branches"])
    branch_total = int(totals["num_branches"])
    combined_covered = line_covered + branch_covered
    combined_total = line_total + branch_total
    metrics = (
        ("line", line_covered, line_total),
        ("branch", branch_covered, branch_total),
        ("combined", combined_covered, combined_total),
    )
    passed = True
    for name, covered, total in metrics:
        zero_is_full = name == "branch"
        print(f"{name}: {_percent(covered, total, zero_is_full=zero_is_full)}")
        if not _passes(covered, total, zero_is_full=zero_is_full):
            passed = False
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
