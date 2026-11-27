"""Rule values (LLD "Values and configuration"): an invalid combination stops the app at startup."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from rag_core.limits import Limits

ROOT = Path(__file__).resolve().parents[2]


def test_the_answer_reserve_must_leave_time_before_the_answer() -> None:
    values = yaml.safe_load((ROOT / "config/limits.yaml").read_text())
    with pytest.raises(ValidationError, match="answer_reserve"):
        Limits.model_validate({**values, "answer_reserve": values["request_deadline"]})
