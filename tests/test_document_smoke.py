from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from price_parser import __version__


def test_document_smoke_releases_sqlite_handle(tmp_path: Path) -> None:
    output = tmp_path / "document-smoke.json"
    script = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "document_smoke_v0_7_0_rc2.py"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(script.parents[1] / "src")

    completed = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        cwd=script.parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["version"] == __version__
    assert payload["database_revision"] == "0003_commercial_documents"
