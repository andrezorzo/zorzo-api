"""AC2: the dependency rules of LLD "Components and dependencies" hold (import contracts)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_import_contracts_hold() -> None:
    result = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports"), "--no-cache"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Contracts: 3 kept, 0 broken" in result.stdout
