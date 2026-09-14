"""coverage harness 的纯函数和一次执行约束。"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from tests.support import run_module08_coverage as harness


def test_metrics_use_exact_unrounded_line_branch_and_combined_formulas():
    metrics = harness.calculate_metrics(
        {"num_statements": 200, "covered_lines": 191, "num_branches": 40, "covered_branches": 39}
    )
    assert metrics.line_percent == 95.5
    assert metrics.branch_percent == 97.5
    assert metrics.combined_percent == 23000 / 240
    assert harness.coverage_gate(metrics, threshold=80.0)


def test_default_gate_is_70_and_exact_boundary_is_accepted():
    metrics = harness.calculate_metrics(
        {"num_statements": 100, "covered_lines": 70, "num_branches": 100, "covered_branches": 70}
    )
    assert harness.DEFAULT_COVERAGE_THRESHOLD == 70.0
    assert harness.coverage_gate(metrics)


def test_69_999_percent_is_below_gate_without_rounding():
    metrics = harness.calculate_metrics(
        {"num_statements": 100000, "covered_lines": 69999, "num_branches": 0, "covered_branches": 0}
    )
    assert metrics.line_percent == 69.999
    assert not harness.coverage_gate(metrics)


@pytest.mark.parametrize("threshold", [True, False, math.nan, math.inf, -math.inf, -0.001, 100.001])
def test_threshold_rejects_bool_nonfinite_and_out_of_range_values(threshold):
    metrics = harness.calculate_metrics(
        {"num_statements": 100, "covered_lines": 100, "num_branches": 1, "covered_branches": 1}
    )
    with pytest.raises(ValueError):
        harness.coverage_gate(metrics, threshold=threshold)


def test_zero_branch_denominator_is_exactly_full_branch_coverage():
    metrics = harness.calculate_metrics(
        {"num_statements": 4, "covered_lines": 4, "num_branches": 0, "covered_branches": 0}
    )
    assert metrics.branch_percent == 100.0
    assert metrics.combined_percent == 100.0


@pytest.mark.parametrize(
    "totals",
    [
        {"num_statements": 0, "covered_lines": 0, "num_branches": 0, "covered_branches": 0},
        {"num_statements": 4, "covered_lines": 5, "num_branches": 0, "covered_branches": 0},
        {"num_statements": 4, "covered_lines": 4, "num_branches": 2, "covered_branches": 3},
        {"num_statements": 0, "covered_lines": 0, "num_branches": 1, "covered_branches": 0},
        {"num_statements": 1, "covered_lines": -1, "num_branches": 0, "covered_branches": 0},
    ],
)
def test_invalid_denominators_and_numerators_fail_closed(totals):
    with pytest.raises(ValueError):
        harness.calculate_metrics(totals)


def test_tracker_rejects_skip_xfail_and_rerun_counts():
    tracker = harness._PytestTracker()
    tracker.pytest_collection_finish(type("Session", (), {"items": [1, 2, 3]})())
    tracker.pytest_collectreport(type("Report", (), {"nodeid": "collect.py", "outcome": "skipped", "wasxfail": False})())
    tracker.pytest_runtest_logreport(type("Report", (), {"nodeid": "test_setup", "when": "setup", "outcome": "skipped", "wasxfail": False})())
    tracker.pytest_runtest_logreport(type("Report", (), {"nodeid": "test_setup", "when": "teardown", "outcome": "skipped", "wasxfail": False})())
    tracker.pytest_runtest_logreport(type("Report", (), {"nodeid": "test_xfail", "when": "call", "outcome": "skipped", "wasxfail": "reason"})())
    tracker.pytest_runtest_logreport(type("Report", (), {"nodeid": "test_rerun", "when": "call", "outcome": "rerun", "wasxfail": False})())
    tracker.pytest_runtest_logreport(type("Report", (), {"nodeid": "test_rerun", "when": "setup", "outcome": "rerun", "wasxfail": False})())
    assert (tracker.collected, tracker.skipped, tracker.xfailed, tracker.rerun) == (3, 2, 1, 1)


def test_pytest_api_is_called_once_and_propagates_return_code(monkeypatch):
    calls: list[list[str]] = []

    def fake_main(args, *, plugins):
        calls.append(list(args))
        assert len(plugins) == 1
        return 1

    monkeypatch.setattr(harness.pytest, "main", fake_main)
    code, tracker = harness.run_pytest_once(("tests/unit/candidate_research",))
    assert code == 1
    assert tracker.collected == 0
    assert len(calls) == 1
    assert calls[0][-1] == "tests/unit/candidate_research"


@pytest.mark.parametrize("paths", [(), ("",), ("   ",), (123,), "tests/unit/candidate_research"])
def test_run_entrypoints_reject_empty_or_non_string_paths_before_side_effects(monkeypatch, paths):
    monkeypatch.setattr(harness.pytest, "main", lambda *args, **kwargs: pytest.fail("pytest must not start"))
    monkeypatch.setattr(harness.coverage, "Coverage", lambda *args, **kwargs: pytest.fail("coverage must not start"))
    with pytest.raises(ValueError):
        harness.run_pytest_once(paths)
    with pytest.raises(ValueError):
        harness.run_coverage(test_paths=paths)


def test_harness_contract_locks_source_paths_output_and_no_recursive_invocation():
    assert harness.SOURCE_PACKAGE == ("app.candidate_research",)
    assert harness.DEFAULT_TEST_PATHS == (
        "tests/unit/candidate_research",
        "tests/contract/candidate_research",
    )
    assert harness.COVERAGE_OUTPUT == Path("/tmp/module08-coverage.json")
    source = Path("tests/support/run_module08_coverage.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_coverage_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_coverage"
    ]
    assert len(run_coverage_calls) == 1
    assert "run_module08_coverage.py" not in " ".join(harness.DEFAULT_TEST_PATHS)
