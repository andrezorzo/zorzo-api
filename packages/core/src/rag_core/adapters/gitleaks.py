"""Secret scan with the gitleaks binary (pinned; see apps/ingest/AGENTS.md).

`--redact` keeps the secret out of the report; findings carry only the file and the rule id.
"""

import asyncio
import json
from pathlib import Path

from rag_core.ports import Finding


class GitleaksError(RuntimeError):
    pass


class GitleaksScanner:
    def __init__(self, binary: str = "gitleaks") -> None:
        self.binary = binary

    async def scan(self, folder: Path) -> list[Finding]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "dir", "--no-banner", "--redact", "--log-level", "error",
            "--report-format", "json", "--report-path", "-", str(folder),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, _ = await proc.communicate()
        # gitleaks exits 1 when it finds leaks; anything else is a failure of the scan itself.
        if proc.returncode not in (0, 1):
            raise GitleaksError(f"gitleaks failed with exit code {proc.returncode}")
        report = json.loads(out or b"[]")
        return [Finding(Path(item["File"]).name, f"secret:{item['RuleID']}") for item in report]
