# What the link rule and the canary check deliberately don't catch

Record of F01-T08's output checks (guard layer `L5`, LLD "6.4 Answer contract and verification"): what stays
open on purpose, and why. The checks stop the common ways of leaking the marker or linking out; each item
below was weighed and left, because closing it costs more than the harm it would stop.

## Links

- **Bare hosts (`evil.com/login`, no scheme, no `www.`).** Not checked. The page shows a claim with
  `textContent`, so nothing in a claim is clickable; only an explicit URL (`scheme://…`, `www.…`, `//host`)
  is something a visitor might copy and open. Detecting bare hosts needs a list of what counts as a top-level
  domain, and it keeps misreading names and files (`Node.js`, `socket.io`, `os.path`, `main.py`,
  `attacker.sh`) or missing real hosts (`Evil.com`, internationalized names). Accepted residual risk.
- **Schemes without slashes other than `javascript:`, `data:` and `vbscript:`** (`mailto:`, `tel:`, `file:`,
  `about:`). Not checked: they are plain text in a claim, and a list that includes them strikes true claims
  (`Profile:` ends in `file:`, `Intel:` in `tel:`). A scheme with slashes (`ftp://`, `file://`) is refused.
- **A link written with letters that only look like the allowed host** (a Cyrillic `е` in a host) is refused
  by the plain-ASCII host rule, but text that merely *spells* an allowed name with look-alike letters in
  prose is not a link and is not checked.
- **A second link after a closer outside the delimiter set** (`https://github.com/a)//evil.com`,
  `https://github.com/a»//evil.com`). The nested-link check looks for another `://` anywhere, and for `www.`
  or `//` right after `, ; | " ' ( < [`; other closers aren't in that set. It needs a contrived output (a
  model writing a second URL glued to the first with such a character), and the text is shown as text, not
  as a link.
- **A URL split by the model across words or lines** (`https://` then a space) is two pieces of text, not a
  link.

## Canary

- **Homoglyph or confusable spellings of the canary** (Cyrillic letters standing for Latin ones, and the
  like). Not folded: it would take a confusables table that has to be kept current, and a model asked to
  write the marker "in a disguise" would need to know the marker already to disguise it. The comparison does
  fold case, accents and combining marks, compatibility forms (fullwidth, ligatures) and every character
  that is not a letter or a digit.
- **Encoded or reversed leaks** (base64, hex, ROT13, the canary written backwards, one letter per sentence).
  Not decoded: the check reads the text the visitor will read, and an encoded marker is not readable as the
  marker. A visitor who can decode it already holds what they would learn from it, and the marker protects
  nothing but the fact that the prompt leaked.
- **A canary the model leaks in a language of its own** (translated words if the canary were words). The
  canary is a random string, not words, so there is nothing to translate.
- **A leak through a channel other than the answer's text** (the stored trace, the logs). Those never carry
  the prompt: provider error messages are logged by kind only, and the stored trace goes through the shared
  redactor, which also removes the canary (F01-T10).
