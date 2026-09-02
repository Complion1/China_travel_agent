"""Module 08 单进程 coverage Gate。

该脚本故意只公开纯函数和一次 pytest/coverage 编排入口，便于合同测试在
不递归启动自身的情况下验证门槛、输出路径和失败关闭行为。
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Mapping, Sequence

import coverage
import pytest
from coverage.exceptions import CoverageException


COVERAGE_OUTPUT = Path("/tmp/module08-coverage.json")
SOURCE_PACKAGE = ("app.candidate_research",)
DEFAULT_COVERAGE_THRESHOLD = 80.0
DEFAULT_TEST_PATHS = (
    "tests/unit/candidate_research",
    "tests/contract/candidate_research",
)


@dataclass(frozen=True)
class CoverageMetrics:
    statements: int
    covered_lines: int
    branches: int
    covered_branches: int
    line_percent: float
    branch_percent: float
    combined_percent: float


def calculate_metrics(totals: Mapping[str, object]) -> CoverageMetrics:
    """从 coverage JSON totals 计算未舍入的三项指标并校验分母。"""

    names = ("num_statements", "covered_lines", "num_branches", "covered_branches")
    values: dict[str, int] = {}
    for name in names:
        value = totals.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid coverage denominator or numerator: {name}")
        values[name] = value
    statements = values["num_statements"]
    covered_lines = values["covered_lines"]
    branches = values["num_branches"]
    covered_branches = values["covered_branches"]
    if statements == 0 or covered_lines > statements or covered_branches > branches:
        raise ValueError("invalid coverage totals")
    combined_denominator = statements + branches
    if combined_denominator == 0:
        raise ValueError("combined coverage denominator is zero")
    line_percent = 100.0 * covered_lines / statements
    branch_percent = 100.0 if branches == 0 else 100.0 * covered_branches / branches
    combined_percent = 100.0 * (covered_lines + covered_branches) / combined_denominator
    return CoverageMetrics(
        statements=statements,
        covered_lines=covered_lines,
        branches=branches,
        covered_branches=covered_branches,
        line_percent=line_percent,
        branch_percent=branch_percent,
        combined_percent=combined_percent,
    )


def coverage_gate(metrics: CoverageMetrics, *, threshold: float = DEFAULT_COVERAGE_THRESHOLD) -> bool:
    """按未舍入指标执行 line/branch/combined 共同门槛。"""

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold < 0
        or threshold > 100
    ):
        raise ValueError("coverage threshold must be between 0 and 100")
    return all(
        value >= threshold
        for value in (metrics.line_percent, metrics.branch_percent, metrics.combined_percent)
    )


@dataclass
class _PytestTracker:
    collected: int = 0
    skipped: int = 0
    xfailed: int = 0
    rerun: int = 0
    _skip_nodes: set[str] = dataclass_field(default_factory=set, repr=False)
    _xfail_nodes: set[str] = dataclass_field(default_factory=set, repr=False)
    _rerun_nodes: set[str] = dataclass_field(default_factory=set, repr=False)

    def pytest_collection_finish(self, session: object) -> None:
        self.collected = len(getattr(session, "items", ()))

    @staticmethod
    def _node_id(report: object) -> str:
        return str(getattr(report, "nodeid", None) or id(report))

    def _record_skip(self, report: object) -> None:
        node_id = self._node_id(report)
        if getattr(report, "wasxfail", None):
            if node_id not in self._xfail_nodes:
                self._xfail_nodes.add(node_id)
                self.xfailed += 1
        elif node_id not in self._skip_nodes:
            self._skip_nodes.add(node_id)
            self.skipped += 1

    def pytest_collectreport(self, report: object) -> None:
        if getattr(report, "outcome", None) == "skipped":
            self._record_skip(report)

    def pytest_runtest_logreport(self, report: object) -> None:
        outcome = getattr(report, "outcome", None)
        if outcome == "rerun":
            node_id = self._node_id(report)
            if node_id not in self._rerun_nodes:
                self._rerun_nodes.add(node_id)
                self.rerun += 1
            return
        if outcome == "skipped":
            self._record_skip(report)


def _validate_test_paths(test_paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(test_paths, (str, bytes)) or not isinstance(test_paths, SequenceABC):
        raise ValueError("test paths must be a non-empty sequence of strings")
    paths = tuple(test_paths)
    if not paths or any(type(path) is not str or not path.strip() for path in paths):
        raise ValueError("test paths must be a non-empty sequence of strings")
    return paths


def run_pytest_once(test_paths: Sequence[str] = DEFAULT_TEST_PATHS) -> tuple[int, _PytestTracker]:
    """通过 pytest API 恰好执行一次指定测试集合。"""

    test_paths = _validate_test_paths(test_paths)
    tracker = _PytestTracker()
    exit_code = pytest.main(["-q", "-p", "no:cacheprovider", *test_paths], plugins=[tracker])
    return int(exit_code), tracker


def run_coverage(
    *,
    output_path: Path = COVERAGE_OUTPUT,
    test_paths: Sequence[str] = DEFAULT_TEST_PATHS,
) -> tuple[int, CoverageMetrics | None, _PytestTracker]:
    """单进程运行 pytest 与 coverage，并将 JSON 写到指定路径。"""

    test_paths = _validate_test_paths(test_paths)
    normalized_output = str(output_path).replace("\\", "/")
    if not (output_path.is_absolute() or normalized_output.startswith("/")):
        raise ValueError("coverage output path must be absolute")
    tracker = _PytestTracker()
    cov = coverage.Coverage(source=list(SOURCE_PACKAGE), branch=True, data_file="/tmp/.module08-coverage")
    cov.start()
    try:
        exit_code = int(pytest.main(["-q", "-p", "no:cacheprovider", *test_paths], plugins=[tracker]))
    finally:
        cov.stop()
        cov.save()
    metrics: CoverageMetrics | None = None
    try:
        cov.json_report(outfile=str(output_path), pretty_print=False)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        totals = payload.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("coverage JSON totals missing")
        metrics = calculate_metrics(totals)
    except (CoverageException, OSError, TypeError, ValueError, json.JSONDecodeError):
        exit_code = max(exit_code, 1)
    if tracker.collected == 0 or tracker.skipped or tracker.xfailed or tracker.rerun:
        exit_code = max(exit_code, 1)
    if metrics is None or not coverage_gate(metrics):
        exit_code = max(exit_code, 1)
    return exit_code, metrics, tracker


def main() -> int:
    exit_code, metrics, tracker = run_coverage()
    print(f"collected={tracker.collected} skipped={tracker.skipped} xfail={tracker.xfailed} rerun={tracker.rerun}")
    if metrics is None:
        print("statements=0 covered_lines=0 branches=0 covered_branches=0 line=0.0 branch=0.0 combined=0.0")
    else:
        print(
            "statements={0.statements} covered_lines={0.covered_lines} branches={0.branches} "
            "covered_branches={0.covered_branches} line={0.line_percent:.12f} "
            "branch={0.branch_percent:.12f} combined={0.combined_percent:.12f}".format(metrics)
        )
    print(f"coverage_json={COVERAGE_OUTPUT}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
