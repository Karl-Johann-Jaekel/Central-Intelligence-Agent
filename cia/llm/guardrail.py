"""Guardrails (plan section 5.7): every number must occur in a tool result, every statement needs
evidence that resolves to something the run actually saw.

The number check is the same as the BIS (`llm/guardrail.py` there): numbers are compared as
values, not strings ("1.234,5" equals 1234.5), a number written with fewer decimals matches an
input value that rounds to it (input 14,25 -> "14,3" passes), dates must appear verbatim."""

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit

DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{4}\b|\b\d{1,2}/\d{4}\b")
# German notation: thousands separator "." (groups of three), decimal comma.
NUMBER = re.compile(
    r"(?<![\d,.])[-−+]?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\d])|(?<![\d,.])[-−+]?\d+(?:,\d+)?(?![\d])"
)
# Digits joined by dots that are not thousands groups (e.g. "12.5"): English notation the NUMBER
# pattern would split into "12" and an ignored "5". Never accepted.
DOTTED = re.compile(r"(?<![\d,.])\d+(?:\.\d+)+(?:,\d+)?(?![\d,.])")
THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")


def _parse(token: str) -> Decimal:
    clean = token.replace("−", "-").replace("+", "").replace(".", "").replace(",", ".")
    return Decimal(clean)


def extract(text: str) -> tuple[set[str], list[str]]:
    """(dates, number tokens) found in text."""
    dates = set(DATE.findall(text))
    without_dates = DATE.sub(" ", text)
    return dates, NUMBER.findall(without_dates)


def _decimals(token: str) -> int:
    return len(token.split(",", 1)[1]) if "," in token else 0


def _matches(token: str, allowed: list[Decimal]) -> bool:
    value = abs(_parse(token))
    quantum = Decimal(1).scaleb(-_decimals(token))
    return any(abs(a).quantize(quantum, rounding=ROUND_HALF_UP) == value for a in allowed)


def unsupported_numbers(output: str, source: str) -> list[str]:
    """Numbers or dates in `output` that do not occur in `source`. Empty list = passes.

    `source` must be in the same notation as `output` (German); see `german()`."""
    source_dates, source_tokens = extract(source)
    allowed = [_parse(t) for t in source_tokens]
    problems = sorted(set(DATE.findall(output)) - source_dates)
    malformed: list[str] = []

    def drop_malformed(match: re.Match[str]) -> str:
        if THOUSANDS.fullmatch(match.group()):
            return match.group()
        malformed.append(match.group())
        return " "

    text = DOTTED.sub(drop_malformed, DATE.sub(" ", output))
    problems += malformed
    problems += [t for t in NUMBER.findall(text) if not _matches(t, allowed)]
    return problems


def german(value: object) -> str:
    """Tool results as text in German notation, so `unsupported_numbers` can compare them.

    JSON floats use a decimal point; without converting, 12.5 would read as 125 (thousands dot)."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int | float):
        text = repr(float(value)) if isinstance(value, float) else str(value)
        if "e" in text or "E" in text:
            text = format(Decimal(text), "f")
        return text.replace(".", ",")
    if isinstance(value, dict):
        return " ".join(f"{k} {german(v)}" for k, v in value.items())
    if isinstance(value, list | tuple):
        return " ".join(german(v) for v in value)
    return str(value)


def canonical_path(path: str) -> str:
    """BI API path with sorted query parameters, so evidence written by the model matches the
    request the tool actually made regardless of parameter order."""
    parts = urlsplit(path)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return f"{parts.path}?{query}" if query else parts.path


def unresolved_evidence(
    refs: Iterable[dict], tool_paths: Iterable[str], known_ids: Iterable[str]
) -> list[dict]:
    """Evidence refs (common.v1 `evidence_ref`) that point to nothing the run saw. Empty = passes.

    `query` refs must name a BI API path a tool called in this run; `insight`, `finding`, `task`
    and `action` refs must name a known id. `chunk` refs cannot resolve before BI A4."""
    paths = {canonical_path(p) for p in tool_paths}
    ids = {str(i).lower() for i in known_ids}
    unresolved = []
    for ref in refs:
        kind = ref.get("kind")
        if kind == "query":
            ok = canonical_path(str(ref.get("ref", ""))) in paths
        elif kind in ("insight", "finding", "task", "action"):
            ok = str(ref.get("id", "")).lower() in ids
        else:
            ok = False
        if not ok:
            unresolved.append(ref)
    return unresolved
