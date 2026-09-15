"""Import boundary contract: package import performs no runtime I/O."""

from __future__ import annotations

import os
import subprocess
import sys


def test_package_import_does_not_require_environment_or_database() -> None:
    environment = os.environ.copy()
    for name in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        environment.pop(name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.artifact_persistence as p; print(','.join(p.__all__))",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip().startswith("ArtifactPersistenceSettings,")
