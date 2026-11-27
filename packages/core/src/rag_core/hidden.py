"""Characters that hide text from a reader but not from a model (LLD "Security", input and trace redaction):
control (Cc), format (Cf, which holds the zero-width and bidi controls) and lone surrogate (Cs, which JSON
lets through and UTF-8 can't encode) characters. One rule for the question and for the stored trace.
"""

import re
import sys
import unicodedata

HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def _character_class() -> str:
    """The regex character class of every code point in `HIDDEN_CATEGORIES`, as ranges."""
    ranges: list[tuple[int, int]] = []
    for cp in range(sys.maxunicode + 1):
        if unicodedata.category(chr(cp)) in HIDDEN_CATEGORIES:
            if ranges and ranges[-1][1] == cp - 1:
                ranges[-1] = (ranges[-1][0], cp)
            else:
                ranges.append((cp, cp))
    return "".join(
        re.escape(chr(lo)) if lo == hi else f"{re.escape(chr(lo))}-{re.escape(chr(hi))}" for lo, hi in ranges
    )


HIDDEN = re.compile("[" + _character_class() + "]")


def strip_hidden(text: str, keep: str = "") -> str:
    """The text without its hidden characters, except those listed in `keep`."""
    return HIDDEN.sub(lambda m: m.group() if m.group() in keep else "", text)
