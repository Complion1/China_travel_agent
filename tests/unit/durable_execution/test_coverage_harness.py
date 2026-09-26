"""Decision tests for the Module 11 coverage tool; never invoke its full run()."""

from __future__ import annotations

from types import SimpleNamespace

from tests.support.run_module11_coverage import (
    PytestMetrics,
    SOURCE_DIR,
    coverage_summary,
    evaluate,
    new_coverage,
    pytest_arguments,
)


def _totals(lines: int = 7, statements: int = 10, branches: int = 7, total_branches: int = 10) -> dict[str, int]:
    return {
        "num_statements": statements,
        "covered_lines": lines,
        "missing_lines": statements - lines,
        "num_branches": total_branches,
        "covered_branches": branches,
        "missing_branches": total_branches - branches,
    }


def _result(**changes: object) -> dict[str, object]:
    inputs: dict[str, object] = {
        "pytest_exit_code": 0,
        "collected": 1,
        "collection_errors": 0,
        "counts": {"passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "rerun": 0},
        "totals": _totals(),
        "coverage_data_present": True,
        "coverage_json_present": True,
        "missing_source_files": [],
        "harness_errors": [],
    }
    inputs.update(changes)
    return evaluate(**inputs)


def test_raw_threshold_rejects_a_value_that_displays_as_70_percent() -> None:
    just_below = _result(totals=_totals(6999, 10000, 7000, 10000))
    assert round(just_below["combined_percent"], 2) == 70.00
    assert "line_below_threshold" in just_below["failure_reasons"]
    assert "combined_below_threshold" in just_below["failure_reasons"]
    assert not just_below["gate_passed"]
    assert _result()["gate_passed"]


def test_zero_denominator_and_missing_capture_fail_closed() -> None:
    no_branches = _result(totals=_totals(7, 10, 0, 0))
    assert "branch_zero_denominator" in no_branches["failure_reasons"]
    assert not no_branches["gate_passed"]
    no_data = _result(coverage_data_present=False)
    assert "coverage_data_missing" in no_data["failure_reasons"]
    assert no_data["harness_exit_code"] == 1
    no_report = _result(totals=None, coverage_json_present=False)
    assert "coverage_json_missing" in no_report["failure_reasons"]


def test_test_failures_empty_collection_and_exception_state_fail_closed() -> None:
    assert "failed" in _result(counts={"passed": 0, "failed": 1, "skipped": 0, "xfail": 0, "rerun": 0})["failure_reasons"]
    assert "collected_zero" in _result(collected=0, counts={"passed": 0, "failed": 0, "skipped": 0, "xfail": 0, "rerun": 0})["failure_reasons"]
    assert "pytest_nonzero_or_exception" in _result(pytest_exit_code=None, harness_errors=["pytest:RuntimeError"])["failure_reasons"]
    assert "collection_errors" in _result(collection_errors=1)["failure_reasons"]
    for status in ("skipped", "xfail", "rerun"):
        counts = {"passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "rerun": 0}
        counts[status] = 1
        assert status in _result(counts=counts)["failure_reasons"]


def test_report_hooks_count_setup_failure_xfail_and_rerun() -> None:
    metrics = PytestMetrics()
    metrics.pytest_collection_finish(SimpleNamespace(items=[1, 2, 3]))
    reports = (
        ("a", "setup", "failed", False),
        ("b", "call", "skipped", "reason"),
        ("c", "call", "rerun", False),
        ("c", "call", "passed", False),
    )
    for nodeid, when, outcome, wasxfail in reports:
        metrics.pytest_runtest_logreport(SimpleNamespace(
            nodeid=nodeid, when=when, outcome=outcome, wasxfail=wasxfail,
            failed=outcome == "failed", skipped=outcome == "skipped", passed=outcome == "passed",
        ))
    assert metrics.collected == 3
    assert metrics.result_counts() == {"passed": 1, "failed": 1, "skipped": 0, "xfail": 1, "rerun": 1}


def test_source_configuration_isolated_from_project_coverage_settings(tmp_path) -> None:
    measured = new_coverage(tmp_path / ".coverage")
    assert measured.get_option("run:branch") is True
    assert measured.get_option("run:source") == [str(SOURCE_DIR)]
    assert measured.get_option("report:fail_under") == 0.0
    assert measured.get_option("report:include_namespace_packages") is True
    assert measured.get_option("run:omit") == []
    assert pytest_arguments() == [
        "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
        "tests/unit/durable_execution", "tests/contract/durable_execution",
        "tests/integration/durable_execution",
    ]
    assert coverage_summary(_totals())["combined_numerator"] == 14
