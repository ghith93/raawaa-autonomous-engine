"""Remove credentials from text that is about to become permanently public.

The batch reporter renders a failing run's stderr tail into an Actions
annotation and a step summary. Annotations and step summaries are readable by
anyone who can read the repository, and this repository is public. The tail is
already bounded and flattened, but bounding is not redaction: a traceback whose
exception message embeds a header, a token fragment or an environment value
would be published verbatim.

This module is deliberately narrow and explainable. It masks what has a known
shape (provider token prefixes, bearer headers, sensitive key/value pairs,
long high-entropy runs) and leaves everything else alone, because a redactor
that eats stack frames destroys the diagnostic value it exists to protect.

Known limitation, stated rather than hidden: a secret shorter than 48
characters with no recognisable prefix and no sensitive key attached will
survive. Forty-character hex strings are deliberately left intact because they
are usually commit ids and are worth far more to a diagnosis than they cost.
"""

from __future__ import annotations

import re

REPLACEMENT = "[redacted]"

#: Provider and platform token prefixes with a fixed shape.
_PREFIX_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
)

#: Keys whose value is a credential, in any of the shapes an error message takes.
#: The key may be embedded in a longer identifier (``ENGINE_API_TOKEN``), so the
#: stems are matched with surrounding identifier characters rather than as whole
#: words -- a word boundary would miss every environment variable ever named.
_SENSITIVE_STEMS = (
    r"authorization",
    r"proxy-authorization",
    r"cf-access-client-secret",
    r"cf-access-client-id",
    r"x-api-key",
    r"api[_-]?key",
    r"apikey",
    r"access[_-]?token",
    r"refresh[_-]?token",
    r"id[_-]?token",
    r"private[_-]?key",
    r"client[_-]?secret",
    r"secret",
    r"password",
    r"passwd",
    r"credential",
    r"token",
)
_KEY_STEM = "(?:" + "|".join(_SENSITIVE_STEMS) + ")"
_JSON_KEY_PATTERN = re.compile(
    r"(?i)(\"[A-Za-z0-9_.\-]*" + _KEY_STEM + r"[A-Za-z0-9_.\-]*\"\s*:\s*)"
    r"((?!\[redacted\])[^,}]+)"
)
#: The value may carry an authentication scheme ("Bearer <token>"), so the
#: scheme is consumed with the value; otherwise the token would survive beside
#: the marker.
#: Identifiers around the stem are bounded rather than greedy: an unbounded
#: [A-Za-z0-9_.-]* on both sides makes a 50 KB traceback backtrack quadratically,
#: and a redactor that turns a 2-second report into a 3-minute one will be
#: removed by whoever has to wait for it.
_KEY_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_.\-])([A-Za-z0-9_.\-]{0,40}" + _KEY_STEM + r"[A-Za-z0-9_.\-]{0,20})(\s*[:=]\s*)"
    r"((?:(?:bearer|basic|token)\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;)\]\"']+))"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}")

#: A long unbroken run with no spaces is almost always a key, a hash or an id.
#: 48 characters keeps 40-character commit ids readable.
#: "/" is deliberately excluded: it is path punctuation, and including it made
#: every traceback file path look like one opaque token (File "[redacted].py").
_OPAQUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+=_\-])[A-Za-z0-9+=_\-]{48,}(?![A-Za-z0-9+=_\-])"
)


def redact(text: str) -> str:
    """Return ``text`` with credential-shaped material replaced by a marker."""
    if not text:
        return text
    result = text
    for pattern in _PREFIX_PATTERNS:
        result = pattern.sub(REPLACEMENT, result)
    # Key/value first: it consumes the whole value, so "Authorization: Bearer x"
    # becomes one marker instead of two.
    result = _JSON_KEY_PATTERN.sub(lambda match: match.group(1) + REPLACEMENT, result)
    result = _KEY_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REPLACEMENT}", result
    )
    result = _BEARER_PATTERN.sub(lambda match: f"{match.group(1)} {REPLACEMENT}", result)
    result = _OPAQUE_PATTERN.sub(REPLACEMENT, result)
    # Normalise adjacent markers left behind by a second pass, so redaction is
    # idempotent: a caller may safely re-redact text that was already redacted.
    result = re.sub(r"(\[redacted\])+", REPLACEMENT, result)
    result = result.replace("[redacted]]", REPLACEMENT)
    return result


def contains_secret_shape(text: str) -> bool:
    """True when ``text`` still trips a redaction rule. Used by tests and callers."""
    return redact(text) != text
