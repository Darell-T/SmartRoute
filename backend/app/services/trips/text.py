"""Rider-facing text sanitization for trip narration.

Leaf module: no internal trips dependencies. Scoring, candidates, and incidents
all import ``_safe_text`` from here, which is why it lives on its own.
"""

import re

_TTS_ABBREVIATIONS = [
    (re.compile(r'\bSt\b'), 'Street'),
    (re.compile(r'\bSq\b'), 'Square'),
    (re.compile(r'\bAv\b'), 'Avenue'),
    (re.compile(r'\bAve\b'), 'Avenue'),
    (re.compile(r'\bBlvd\b'), 'Boulevard'),
    (re.compile(r'\bHwy\b'), 'Highway'),
    (re.compile(r'\bPkwy\b'), 'Parkway'),
    (re.compile(r'\bCtr\b'), 'Center'),
    (re.compile(r'\bRd\b'), 'Road'),
    (re.compile(r'\bPl\b'), 'Place'),
    (re.compile(r'\bDr\b'), 'Drive'),
    (re.compile(r'\bLn\b'), 'Lane'),
]

_INTERNAL_LEAK_PATTERN = re.compile(
    r"\b(backend|frontend|api|json|payload|database|sql|gtfs|server|model|prompt|route index)\b",
    re.IGNORECASE,
)

_TELEMETRY_LEAK_PATTERN = re.compile(
    r"(RecordedAtTime|ProgressStatus|noProgress|layover|route_id|stop_id|stalled_minutes|\bis\s+stalled\s+for\s+\d+\s+minutes?\b)",
    re.IGNORECASE,
)


def _expand_abbreviations(text: str) -> str:
    for pattern, replacement in _TTS_ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize_recommendation(text: str) -> str:
    if not _INTERNAL_LEAK_PATTERN.search(text) and not _TELEMETRY_LEAK_PATTERN.search(text):
        return text
    print("[trip] model output included internal/telemetry details; using rider-facing fallback")
    return (
        "Take the next recommended train from your departure station, then follow the transfer shown on your map, sir. "
        "There may be minor operational delays, and total time should stay close to the displayed estimate."
    )


def _safe_text(value: object, max_len: int = 150) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"
