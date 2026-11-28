"""Local language detection (LLD "1. Code structure", language detection): fast-langdetect's bundled `lite`
fastText model. It names 176 languages, so a question in another language reads as itself and not as one of
the domain's."""

import re
from typing import Literal

from fast_langdetect.infer import LangDetectConfig, LangDetector

# `lite` loads the bundled `lid.176.ftz` and never downloads; the default `auto` would download 126 MB at the
# first call. `max_input_length=None` keeps the library from logging a line on each long question.
MODEL: Literal["lite"] = "lite"
CODE = re.compile(r"^[a-z]{2,3}$")
# `warm` reads this once, so a broken model stops the API at startup.
WARM_TEXT = "Which languages does the site answer in?"


class FastTextLanguageDetector:
    def __init__(self) -> None:
        self._detector = LangDetector(LangDetectConfig(model=MODEL, max_input_length=None))

    def detect(self, text: str) -> str | None:
        found = self._detector.detect(text.replace("\n", " "), k=1)  # fastText reads one line
        code = str(found[0]["lang"]).lower() if found else ""
        return code if CODE.match(code) else None

    def warm(self) -> None:
        if self.detect(WARM_TEXT) is None:
            raise RuntimeError("the language detector did not load")
