"""M13 whole-package coverage with official multiprocessing collection."""
from pathlib import Path
import json
import os
import tempfile
import sys

import coverage
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support.run_module11_coverage import PytestMetrics, evaluate


def main():
    root = Path(__file__).resolve().parents[2]
    source = root / "app" / "demo_workflow"
    with tempfile.TemporaryDirectory(prefix="m13-coverage-") as directory:
        output = Path(directory)
        config = output / "coverage.ini"
        config.write_text(f"[run]\nsource = {source}\nbranch = True\nparallel = True\nconcurrency = multiprocessing\ndata_file = {output / '.coverage'}\n", encoding="utf-8")
        measured = coverage.Coverage(config_file=str(config))
        metrics = PytestMetrics()
        measured.start()
        try:
            result = pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", "--import-mode=importlib",
                "tests/unit/demo_workflow", "tests/contract/demo_workflow", "tests/integration/demo_workflow"], plugins=[metrics])
        finally:
            measured.stop()
            measured.save()
        measured = coverage.Coverage(config_file=str(config), data_suffix=False)
        measured.combine()
        measured.save()
        report_path = output / "coverage.json"
        measured.json_report(outfile=str(report_path))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        expected = {p.relative_to(root).as_posix() for p in source.glob("*.py")}
        summary = evaluate(pytest_exit_code=int(result), collected=metrics.collected,
            collection_errors=metrics.collection_errors, counts=metrics.result_counts(), totals=report["totals"],
            coverage_data_present=(output / ".coverage").exists(), coverage_json_present=True,
            missing_source_files=sorted(expected - report["files"].keys()), harness_errors=[])
        measured.report(show_missing=True)
        print(json.dumps(summary, sort_keys=True), flush=True)
        return summary["harness_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
