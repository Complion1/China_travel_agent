"""09-6 覆盖率 harness 的固定输入与未舍入门槛合同。"""

from __future__ import annotations

from pathlib import Path

from tests.support.run_module09_coverage import (
    DEFAULT_COVERAGE_THRESHOLD,
    DEFAULT_TEST_PATHS,
    CoverageMetrics,
    calculate_metrics,
    coverage_gate,
    extract_missing_coverage,
    format_missing_coverage,
)


def test_coverage_harness_uses_module09_unit_and_contract_paths() -> None:
    assert DEFAULT_TEST_PATHS == (
        "tests/unit/itinerary_planning",
        "tests/contract/itinerary_planning",
    )
    assert DEFAULT_COVERAGE_THRESHOLD == 70.0
    assert Path("tests/support/run_module09_coverage.py").is_file()


def test_coverage_gate_checks_unrounded_line_branch_and_combined_metrics() -> None:
    metrics = calculate_metrics({
        "num_statements": 10,
        "covered_lines": 7,
        "num_branches": 10,
        "covered_branches": 7,
    })
    assert isinstance(metrics, CoverageMetrics)
    assert metrics.line_percent == 70.0
    assert metrics.branch_percent == 70.0
    assert metrics.combined_percent == 70.0
    assert coverage_gate(metrics)


def test_coverage_diagnostics_are_source_scoped_and_deterministic() -> None:
    payload = {
        "files": {
            "app\\itinerary_planning\\service.py": {
                "missing_lines": [397, 396],
                "missing_branches": [[396, 398], [396, 397]],
            },
            "app/itinerary_planning/__init__.py": {
                "missing_lines": [12],
                "missing_branches": [[11, 13]],
            },
            "tests/secret_payload.py": {
                "missing_lines": [1],
                "missing_branches": [[1, 2]],
            },
        },
    }

    assert extract_missing_coverage(payload) == (
        (
            "app/itinerary_planning/__init__.py:12",
            "app/itinerary_planning/service.py:396",
            "app/itinerary_planning/service.py:397",
        ),
        (
            "app/itinerary_planning/__init__.py:11->13",
            "app/itinerary_planning/service.py:396->397",
            "app/itinerary_planning/service.py:396->398",
        ),
    )
    assert format_missing_coverage(payload) == (
        "missing_lines=app/itinerary_planning/__init__.py:12,app/itinerary_planning/service.py:396,app/itinerary_planning/service.py:397",
        "missing_branches=app/itinerary_planning/__init__.py:11->13,app/itinerary_planning/service.py:396->397,app/itinerary_planning/service.py:396->398",
    )
