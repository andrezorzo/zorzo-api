"""Secret scan with the gitleaks binary (pinned; see apps/ingest/AGENTS.md).

`--redact` keeps the secret out of the report; findings carry only the file and the rule id.
"""

import asyncio
import json
from pathlib import Path

from rag_core.ports import Finding

LEAKS_FOUND = 3


class GitleaksError(RuntimeError):
    pass


class GitleaksScanner:
    def __init__(self, binary: str = "gitleaks") -> None:
        self.binary = binary

    async def scan(self, folder: Path) -> list[Finding]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "dir", "--no-banner", "--redact", "--log-level", "error",
            "--exit-code", str(LEAKS_FOUND), "--report-format", "json", "--report-path", "-", str(folder),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, _ = await proc.communicate()
        # gitleaks also exits 1 on its own fatal errors, so leaks get a code of their own; any other code,
        # or no report, means the scan didn't run and the gate must fail closed.
        if proc.returncode not in (0, LEAKS_FOUND):
            raise GitleaksError(f"gitleaks failed with exit code {proc.returncode}")
        try:
            report = json.loads(out)
        except ValueError as exc:
            raise GitleaksError("gitleaks produced no report") from exc
        return [Finding(Path(item["File"]).name, f"secret:{item['RuleID']}") for item in report]
