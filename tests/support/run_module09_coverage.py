"""Module 09 单进程 line/branch/combined coverage Gate。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import coverage
from coverage.exceptions import CoverageException
import pytest


COVERAGE_OUTPUT = Path("/tmp/module09-coverage.json")
SOURCE_PACKAGE = ("app.itinerary_planning",)
DEFAULT_COVERAGE_THRESHOLD = 70.0
DEFAULT_TEST_PATHS = (
    "tests/unit/itinerary_planning",
    "tests/contract/itinerary_planning",
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


def extract_missing_coverage(payload: Mapping[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return deterministic, source-scoped locations from a coverage JSON payload."""
    files = payload.get("files")
    if not isinstance(files, Mapping):
        return (), ()

    missing_lines: list[tuple[str, int]] = []
    missing_branches: list[tuple[str, int, int]] = []
    for filename in files:
        if not isinstance(filename, str):
            continue
        normalized_filename = filename.replace("\\", "/")
        if not normalized_filename.startswith("app/itinerary_planning/"):
            continue
        details = files[filename]
        if not isinstance(details, Mapping):
            continue
        raw_lines = details.get("missing_lines", ())
        if isinstance(raw_lines, Sequence) and not isinstance(raw_lines, (str, bytes)):
            for line in raw_lines:
                if type(line) is int and line > 0:
                    missing_lines.append((normalized_filename, line))
        raw_branches = details.get("missing_branches", ())
        if isinstance(raw_branches, Sequence) and not isinstance(raw_branches, (str, bytes)):
            for branch in raw_branches:
                if (
                    isinstance(branch, Sequence)
                    and not isinstance(branch, (str, bytes))
                    and len(branch) == 2
                    and type(branch[0]) is int
                    and type(branch[1]) is int
                    and branch[0] > 0
                ):
                    missing_branches.append((normalized_filename, branch[0], branch[1]))

    missing_lines.sort(key=lambda item: (item[0], item[1]))
    missing_branches.sort(key=lambda item: (item[0], item[1], item[2]))
    return (
        tuple(f"{filename}:{line}" for filename, line in missing_lines),
        tuple(f"{filename}:{source}->{destination}" for filename, source, destination in missing_branches),
    )


def format_missing_coverage(payload: Mapping[str, object]) -> tuple[str, str]:
    missing_lines, missing_branches = extract_missing_coverage(payload)
    return (
        "missing_lines=" + (",".join(missing_lines) if missing_lines else "none"),
        "missing_branches=" + (",".join(missing_branches) if missing_branches else "none"),
    )


def calculate_metrics(totals: Mapping[str, object]) -> CoverageMetrics:
    names = ("num_statements", "covered_lines", "num_branches", "covered_branches")
    values: dict[str, int] = {}
    for name in names:
        value = totals.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"invalid coverage total: {name}")
        values[name] = value
    statements = values["num_statements"]
    covered_lines = values["covered_lines"]
    branches = values["num_branches"]
    covered_branches = values["covered_branches"]
    if statements == 0 or covered_lines > statements or covered_branches > branches:
        raise ValueError("invalid coverage totals")
    denominator = statements + branches
    if denominator == 0:
        raise ValueError("combined coverage denominator is zero")
    return CoverageMetrics(
        statements=statements,
        covered_lines=covered_lines,
        branches=branches,
        covered_branches=covered_branches,
        line_percent=100.0 * covered_lines / statements,
        branch_percent=100.0 if branches == 0 else 100.0 * covered_branches / branches,
        combined_percent=100.0 * (covered_lines + covered_branches) / denominator,
    )


def coverage_gate(metrics: CoverageMetrics, *, threshold: float = DEFAULT_COVERAGE_THRESHOLD) -> bool:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("coverage threshold must be between 0 and 100")
    return all(value >= threshold for value in (metrics.line_percent, metrics.branch_percent, metrics.combined_percent))


@dataclass
class _PytestTracker:
    collected: int = 0
    skipped: int = 0
    xfailed: int = 0
    rerun: int = 0
    _skip_nodes: set[str] = field(default_factory=set, repr=False)
    _xfail_nodes: set[str] = field(default_factory=set, repr=False)
    _rerun_nodes: set[str] = field(default_factory=set, repr=False)

    def pytest_collection_finish(self, session: object) -> None:
        self.collected = len(getattr(session, "items", ()))

    @staticmethod
    def _node_id(report: object) -> str:
        return str(getattr(report, "nodeid", None) or id(report))

    def pytest_collectreport(self, report: object) -> None:
        if getattr(report, "outcome", None) != "skipped":
            return
        node_id = self._node_id(report)
        was_xfail = bool(getattr(report, "wasxfail", None))
        seen = self._xfail_nodes if was_xfail else self._skip_nodes
        if node_id in seen:
            return
        seen.add(node_id)
        if was_xfail:
            self.xfailed += 1
        else:
            self.skipped += 1

    def pytest_runtest_logreport(self, report: object) -> None:
        outcome = getattr(report, "outcome", None)
        node_id = self._node_id(report)
        if outcome == "rerun":
            if node_id not in self._rerun_nodes:
                self._rerun_nodes.add(node_id)
                self.rerun += 1
        elif outcome == "skipped":
            self.pytest_collectreport(report)


def _validate_paths(test_paths: Sequence[str]) -> tuple[str, ...]:
    if isinstance(test_paths, (str, bytes)) or not isinstance(test_paths, Sequence):
        raise ValueError("test paths must be a non-empty sequence of strings")
    paths = tuple(test_paths)
    if not paths or any(type(path) is not str or not path.strip() for path in paths):
        raise ValueError("test paths must be a non-empty sequence of strings")
    return paths


def _effective_output_path(output_path: Path) -> tuple[Path, str]:
    normalized_output = str(output_path).replace("\\", "/")
    if not output_path.is_absolute() and not normalized_output.startswith("/"):
        raise ValueError("coverage output path must be absolute")
    if os.name == "nt" and normalized_output.startswith("/"):
        temp_path = Path(tempfile.gettempdir()) / "module09-coverage.json"
        return temp_path, str(Path(tempfile.gettempdir()) / ".module09-coverage")
    return output_path, "/tmp/.module09-coverage"


def run_coverage(*, output_path: Path = COVERAGE_OUTPUT, test_paths: Sequence[str] = DEFAULT_TEST_PATHS) -> tuple[int, CoverageMetrics | None, _PytestTracker]:
    paths = _validate_paths(test_paths)
    effective_output, data_file = _effective_output_path(output_path)
    tracker = _PytestTracker()
    cov = coverage.Coverage(source=list(SOURCE_PACKAGE), branch=True, data_file=data_file)
    cov.start()
    try:
        exit_code = int(pytest.main(["-q", "-p", "no:cacheprovider", *paths], plugins=[tracker]))
    finally:
        cov.stop()
        cov.save()
    metrics: CoverageMetrics | None = None
    try:
        cov.json_report(outfile=str(effective_output), pretty_print=False)
        payload = json.loads(effective_output.read_text(encoding="utf-8"))
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
    try:
        effective_output, _ = _effective_output_path(COVERAGE_OUTPUT)
        payload = json.loads(effective_output.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("coverage JSON root missing")
        for diagnostic in format_missing_coverage(payload):
            print(diagnostic)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        print("missing_lines=unavailable")
        print("missing_branches=unavailable")
    print(f"coverage_exit_code={exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
