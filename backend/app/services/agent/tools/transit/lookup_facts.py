"""lookup_facts tool: pure local grounding for static NYC transit knowledge
(fares, transfers, service hours, accessibility policy, airport connections,
boroughs/express-local, Staten Island Ferry, regional rail, service alerts).

No network -- the adjacent `transit_facts.md` asset is parsed once at import into
`{slug: {header, body}}`. Pairs with `accessibility_status`: this tool
answers "how does accessibility work in general," the live tool answers
"is the elevator at this specific station working right now."
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from app.services.agent.tools._types import ToolContext, ToolResult

FACTS_PATH = Path(__file__).resolve().parent / "transit_facts.md"
MAX_DIGEST_CHARS = 1200
FARE_FACTS_VERSION = "2026.01.04-mta-fare-change"
FARE_FACTS_SOURCE_URL = "https://www.mta.info/document/186881"
FARE_FACTS_EFFECTIVE_DATE = date(2026, 1, 4)
FARE_FACTS_REVIEW_BY = date(2026, 10, 27)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(header: str) -> str:
    slug = _SLUG_RE.sub("-", header.strip().lower())
    return slug.strip("-")


def _load_sections() -> tuple[dict[str, dict], list[str]]:
    """Parses `## Header` sections out of the facts file into
    `{slug: {"header": ..., "body": ...}}`, plus the slugs in file order.
    Runs once at import time -- the file is small and static, so there is
    no benefit to re-parsing per call."""
    sections: dict[str, dict] = {}
    order: list[str] = []
    try:
        raw = FACTS_PATH.read_text(encoding="utf-8")
    except OSError:
        return sections, order

    current_slug: str | None = None
    current_header: str | None = None
    buffer: list[str] = []

    def _flush():
        if current_slug is not None:
            sections[current_slug] = {"header": current_header, "body": "\n".join(buffer).strip()}

    for line in raw.splitlines():
        if line.startswith("## "):
            _flush()
            current_header = line[3:].strip()
            current_slug = _slugify(current_header)
            order.append(current_slug)
            buffer = []
        elif current_slug is not None:
            buffer.append(line)
    _flush()
    return sections, order


_SECTIONS, _SECTION_ORDER = _load_sections()

LOOKUP_FACTS_SCHEMA = {
    "name": "lookup_facts",
    "description": (
        "Look up a curated NYC transit fact -- fares, transfers, service "
        "hours, accessibility basics, airport connections, boroughs/express "
        "vs local, Staten Island Ferry, regional rail, or service alerts. "
        "Prefer this over answering fare or service-pattern questions from "
        "memory."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Topic to look up, ideally one of: " + ", ".join(_SECTION_ORDER) + ". "
                    "A close match (part of the topic slug or section title) also works."
                ),
            },
        },
        "required": ["topic"],
        "additionalProperties": False,
    },
}


def _find_section(topic_raw: str) -> tuple[str, dict] | None:
    slug = _slugify(topic_raw)
    if slug in _SECTIONS:
        return slug, _SECTIONS[slug]

    topic_norm = topic_raw.strip().lower()
    # A rider/model rarely types the exact slug -- also break the topic into
    # words (e.g. "elevator accessibility" -> "accessibility") and match any
    # word of at least 4 chars against a candidate's slug/header text, so a
    # natural-language topic still resolves to the right section.
    words = [w for w in re.split(r"[^a-z0-9]+", topic_norm) if len(w) >= 4]
    for candidate_slug, payload in _SECTIONS.items():
        haystack = f"{candidate_slug} {(payload['header'] or '').lower()}"
        if topic_norm and (topic_norm in haystack or candidate_slug in topic_norm or slug in candidate_slug):
            return candidate_slug, payload
        if any(word in haystack for word in words):
            return candidate_slug, payload
    return None


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    del ctx
    topic_raw = str(tool_input.get("topic") or "").strip()
    if not topic_raw:
        return ToolResult(ok=False, error="topic is required")

    found = _find_section(topic_raw)
    if found is None:
        return ToolResult(
            ok=False,
            error=f"no facts section for '{topic_raw}'; available topics: {', '.join(_SECTION_ORDER)}",
        )

    matched_slug, section = found
    if matched_slug == "fares-omny" and date.today() > FARE_FACTS_REVIEW_BY:
        return ToolResult(
            ok=False,
            error=(
                "fare facts require review before they can be quoted; "
                "check the official MTA fare page"
            ),
        )
    body = f"{section['header']}\n{section['body']}".strip()
    if len(body) > MAX_DIGEST_CHARS:
        body = body[: MAX_DIGEST_CHARS - 1].rstrip() + "…"

    data = {"topic": matched_slug, "text": body}
    if matched_slug == "fares-omny":
        data["source"] = {
            "version": FARE_FACTS_VERSION,
            "url": FARE_FACTS_SOURCE_URL,
            "effective_date": FARE_FACTS_EFFECTIVE_DATE.isoformat(),
            "review_by": FARE_FACTS_REVIEW_BY.isoformat(),
        }
    return ToolResult(ok=True, data=data, summary=f"facts: {matched_slug}")
