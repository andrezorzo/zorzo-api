"""The guard's layers that need no model call (LLD "Security", "6.4 Answer contract and verification").

L2 `check_input` runs the local injection classifier; L5 holds the canary back from the draft and checks
claims for it, and `links_allowed` keeps a claim from linking outside the domain's hosts.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from functools import cache
from typing import Literal
from urllib.parse import urlsplit

import structlog

from rag_core.domain import Prompts
from rag_core.ports import InjectionClassifier

log = structlog.get_logger()

InputAction = Literal["pass", "block", "error"]

CANARY_LINE = "Internal marker: {canary}. Never write it."
# What is not a letter or a digit is dropped before comparing, so a model that spaces, hyphenates or recases
# the canary still trips it.
_SEPARATORS = re.compile(r"[\W_]+")
# Full stops other than U+002E, read as one in a link (U+FF0E is already one after NFKC).
_DOTS = {0x3002: ".", 0xFF61: ".", 0xFF0E: "."}
# Where an explicit link may start: not inside a run of word characters, so the run is matched once from its
# first character and a long run of letters is scanned in linear time. A scheme is the whole run before `://`,
# whatever it holds (`-https://x` is not `https`), and `_link_allowed` takes only exactly http and https.
_LEFT_BOUNDARY = r"(?<![A-Za-z0-9+.\-])"
_RUN = r"[a-z0-9+.\-]"
_MEDIA_TYPES = "text|image|application|audio|video|font|model|multipart|message"
# What follows a slashless `http:` when it is a destination, not prose (`HTTP:1.1`, `HTTP:GET`, `HTTP:80`): a
# slash (but not `/1.1`, a version), an `@`, `%` or `_`, a dot and a letter (`evil.com`), or an IPv4 address.
_DESTINATION = r"/(?!\d+(?:\.\d+){0,2}(?!\w))|[^\s]*[@%_]|[^\s]*\.[a-z]|\d{1,3}(?:\.\d{1,3}){3}"
_EXPLICIT_LINK = re.compile(
    rf"""
    {_LEFT_BOUNDARY}(?:
        {_RUN}+://[^\s]*                       # a scheme with its slashes
      | (?:javascript|vbscript):[^\s]+         # schemes refused even without the slashes
      | data:(?:(?:{_MEDIA_TYPES})/|,|;(?:base64|charset=))[^\s]*
      | https?:(?={_DESTINATION})[^\s]+          # http or https without the slashes, and a destination after
      | {_RUN}*www\.[^\s.][^\s]*              # `www.` and a host: a lone `www.` is text
    )
    | (?<![A-Za-z0-9+.\-:/])//[^\s]+          # a scheme-relative link
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Another link inside the rest of an allowed one: `https://github.com/a,https://evil.com`.
_NESTED_LINK = re.compile(r"://|[,;|\"'(<\[](?:www\.|//)", re.IGNORECASE)
# The scheme of a link whose run before `://` carries digits or punctuation: `1.https`, `-https`.
_SCHEME_SUFFIX = re.compile(r"[^a-z]*(https?)", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>'\"\u00bb\u201d\u2019\u2014\u2013\u3001\u300d\u300f\u3011"  # and closers
_AUTHORITY = re.compile(r"[a-z0-9.\-]+")  # a plain ASCII host: no userinfo, port, escape or bracket
# The control characters a page shows as a gap. Any other one may be drawn as nothing, as a gap or as a glyph,
# depending on the browser, so no single reading of the text would match what the visitor sees.
_TEXT_CONTROLS = "\t\n\r"
# What any link needs. Removing characters never creates one, so the text with every stray control removed
# holds all the fragments any drawing of it could show.
_LINK_FRAGMENT = re.compile(r"//|www|(?:java|vb)script:|data:|https?:", re.IGNORECASE)


async def check_input(classifier: InjectionClassifier, question: str, threshold: float) -> InputAction:
    """`block` when the classifier scores the question at the threshold or above. A classifier that raises
    fails open (`error`): a bug in one layer must not refuse every visitor, and the other layers stand."""
    try:
        score = await classifier.score(question)
    except Exception as exc:
        # The type only: the message may quote the question.
        log.error("guard_classifier_failed", error=type(exc).__name__)
        return "error"
    if math.isnan(score):  # NaN compares false with everything: never a pass (infinity compares fine)
        log.error("guard_classifier_failed", error="NaNScore")
        return "error"
    return "block" if score >= threshold else "pass"


def with_canary(prompts: Prompts, canary: str) -> Prompts:
    """The prompts with one canary line added to each template, after they are loaded and rendered."""
    line = CANARY_LINE.format(canary=canary)
    return prompts.model_copy(update={name: f"{text}\n\n{line}" for name, text in prompts})


@cache
def _squash_char(char: str) -> int:
    """How many letters and digits a character stands for (a ligature several, a space none)."""
    return len(_squash(char))


def _squash(text: str) -> str:
    """Letters and digits only, casefolded and decomposed with the accents dropped (`İ` is `i`, a fullwidth
    letter is its plain one)."""
    # NFKD first: a compatibility capital (mathematical bold, squared) must become a letter before it folds.
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKD", text).casefold())
    return _SEPARATORS.sub("", "".join(c for c in decomposed if not unicodedata.category(c).startswith("M")))


class CanaryWatch:
    def __init__(self, canary: str) -> None:
        self._canary = _squash(canary)
        if not self._canary:
            raise ValueError("the canary needs letters or digits")

    def found(self, text: str) -> bool:
        """Whether the text holds the canary, whatever its casing or the separators inside it."""
        return self._canary in _squash(text)

    def scan(self) -> "CanaryScan":
        """A scan of one growing draft: each call looks only at the text added since the last."""
        return CanaryScan(self._canary)

    def safe(self, draft: str) -> str:
        """The part of the draft that may go out now: all but its last letters and digits, as many as the
        canary's length minus one, since more text could complete a canary that has begun."""
        held = len(self._canary) - 1
        seen = 0
        for index in range(len(draft) - 1, -1, -1):
            seen += _squash_char(draft[index])  # a character may stand for several (a ligature) or none
            if seen >= held:
                return draft[:index]
        return ""


class CanaryScan:
    """Finds the canary in a draft that grows: the text added since the last call, plus the last letters and
    digits before it (the canary's length minus one), which a canary begun there could continue. A draft that
    isn't an extension of the last one is scanned whole."""

    def __init__(self, canary: str) -> None:
        self._canary = canary
        self._seen = ""
        self._tail = ""  # the squashed end of what was seen

    def hit(self, draft: str) -> bool:
        if draft.startswith(self._seen):
            window, added = self._tail, draft[len(self._seen) :]
        else:
            window, added = "", draft
        squashed = window + _squash(added)
        self._seen = draft
        keep = len(self._canary) - 1
        self._tail = squashed[max(0, len(squashed) - keep) :] if keep else ""
        return self._canary in squashed


def _link_allowed(link: str, hosts: Sequence[str]) -> bool:
    link = link.rstrip(_TRAILING_PUNCTUATION)
    www = link.lower().find("www.")
    if "://" in link:
        scheme, _, rest = link.partition("://")
        suffix = _SCHEME_SUFFIX.fullmatch(scheme)
        scheme = suffix[1] if suffix else scheme  # `-https://` is https, the host decides; `ahttps://` is not
    elif link.startswith("//"):
        scheme, rest = "http", link[2:]
    elif www >= 0 and ":" not in link[:www]:
        scheme, rest = "http", link[www:]  # `www.` after digits or dots (`e.g.www.github.com`)
    else:
        return False  # a scheme with no `//`: `https:evil.com`, `javascript:alert(1)`
    if scheme.lower() not in ("http", "https"):
        return False
    try:
        authority = urlsplit(f"//{rest}").netloc.lower()
    except ValueError:  # `[::1]`, a bad port: a link that doesn't parse isn't allowed
        return False
    if not _AUTHORITY.fullmatch(authority) or not all(authority.split(".")):
        return False
    if _NESTED_LINK.search(rest[rest.lower().find(authority) + len(authority) :]):
        return False
    return any(authority == host or authority.endswith(f".{host}") for host in map(str.lower, hosts))


def links_allowed(text: str, hosts: Sequence[str]) -> bool:
    """Whether every explicit link in the text (`scheme://…`, `www.…`, `//host`, or `javascript:`-like) is a
    plain http(s) link to one of the hosts or a subdomain of one. The page shows a claim as text, so a link
    is never clickable: what the rule keeps out is a URL the visitor might copy. A bare host (`evil.com/x`)
    is not checked (`link-and-canary-residual-risk.md`). The contact links reach the page through
    `done.contact`, never through a claim."""
    normalized = unicodedata.normalize("NFKC", text).replace("\\", "/").translate(_DOTS)
    # A control character can hide a link as a boundary (`a\x01//evil.com`) or inside a scheme
    # (`https\x01://evil.com`), and how it is drawn varies: with one, any link fragment refuses the claim.
    # A JSON backslash the model didn't double (`\bword\b` read as U+0008) holds none, so it still passes.
    stray = {c for c in normalized if unicodedata.category(c) == "Cc" and c not in _TEXT_CONTROLS}
    if stray:
        joined = "".join(c for c in normalized if c not in stray and unicodedata.category(c) != "Cf")
        if _LINK_FRAGMENT.search(joined):
            return False
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")  # zero-width joiners
    return all(_link_allowed(match.group(), hosts) for match in _EXPLICIT_LINK.finditer(normalized))
