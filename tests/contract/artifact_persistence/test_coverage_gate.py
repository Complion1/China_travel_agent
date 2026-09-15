"""AP-T06 coverage gate contract tests."""

from __future__ import annotations

import inspect

import tests.support.run_module10_coverage as coverage_harness
from tests.support.run_module10_coverage import (
    LINE_BRANCH_THRESHOLD,
    SOURCE_PACKAGE,
    coverage_gate,
    coverage_summary,
    percentage,
    pytest_arguments,
)


def test_percentage_handles_zero_denominators_without_nan() -> None:
    assert percentage(0, 0) == 100.0
    assert percentage(7, 10) == 70.0


def test_summary_uses_raw_line_branch_and_combined_numerators() -> None:
    summary = coverage_summary(
        {
            "num_statements": 8,
            "covered_lines": 6,
            "missing_lines": 2,
            "num_branches": 4,
            "covered_branches": 3,
            "missing_branches": 1,
        }
    )
    assert summary["statements"] == 8
    assert summary["covered_lines"] == 6
    assert summary["branches"] == 4
    assert summary["covered_branches"] == 3
    assert summary["line_percent"] == 75.0
    assert summary["branch_percent"] == 75.0
    assert summary["combined_percent"] == 75.0


def test_gate_requires_nonzero_lines_and_all_three_thresholds() -> None:
    assert coverage_gate(
        {
            "statements": 10,
            "line_percent": LINE_BRANCH_THRESHOLD,
            "branch_percent": LINE_BRANCH_THRESHOLD,
            "combined_percent": LINE_BRANCH_THRESHOLD,
        }
    )
    for key in ("line_percent", "branch_percent", "combined_percent"):
        failed = {
            "statements": 10,
            "line_percent": 100.0,
            "branch_percent": 100.0,
            "combined_percent": 100.0,
        }
        failed[key] = LINE_BRANCH_THRESHOLD - 0.01
        assert not coverage_gate(failed)
    assert not coverage_gate(
        {
            "statements": 0,
            "line_percent": 100.0,
            "branch_percent": 100.0,
            "combined_percent": 100.0,
        }
    )


def test_harness_is_single_process_and_deselects_only_restart_nodes() -> None:
    arguments = pytest_arguments()
    assert SOURCE_PACKAGE == "app.artifact_persistence"
    assert "--import-mode=importlib" in arguments
    assert sum(argument.startswith("--deselect=") for argument in arguments) == 2
    assert all("test_restart_persistence.py::test_restart_" in argument for argument in arguments if argument.startswith("--deselect="))
    source = inspect.getsource(coverage_harness)
    assert "coverage.Coverage" in source
    assert "branch=True" in source
    assert "pytest.main" in source
    assert "subprocess" not in source
