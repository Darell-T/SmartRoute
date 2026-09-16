"""Rider-facing text sanitization for trip narration.

Leaf module: no internal trips dependencies. Scoring, candidates, and incidents
all import ``safe_text`` from here, which is why it lives on its own.
"""

import re

_INTERNAL_LEAK_PATTERN = re.compile(
    r"\b(backend|frontend|api|json|payload|database|sql|gtfs|server|model|prompt|route index)\b",
    re.IGNORECASE,
)

_TELEMETRY_LEAK_PATTERN = re.compile(
    r"(RecordedAtTime|ProgressStatus|noProgress|layover|route_id|stop_id|stalled_minutes|\bis\s+stalled\s+for\s+\d+\s+minutes?\b)",
    re.IGNORECASE,
)


def collapse_whitespace(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def sanitize_recommendation(text: str) -> str:
    if not _INTERNAL_LEAK_PATTERN.search(text) and not _TELEMETRY_LEAK_PATTERN.search(text):
        return text
    print("[trip] model output included internal/telemetry details; using rider-facing fallback")
    return (
        "Take the next recommended train from your departure station, then follow the transfer shown on your map, sir. "
        "There may be minor operational delays, and total time should stay close to the displayed estimate."
    )


def safe_text(value: object, max_len: int = 150) -> str:
    text = collapse_whitespace(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"
