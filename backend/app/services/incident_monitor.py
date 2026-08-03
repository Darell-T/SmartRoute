"""Bounded, candidate-scoped Grok incident research.
This module owns xAI transport, never an official feed or itinerary selection.
The trip service owns caching and the advisor handoff.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

try:  # Trip planning remains available when the optional provider is absent.
    from xai_sdk import AsyncClient
    from xai_sdk.chat import system, user
    from xai_sdk.tools import get_tool_call_type, web_search, x_search
except Exception:  # pragma: no cover - exercised by the configured-client path.
    AsyncClient = None
    system = user = get_tool_call_type = web_search = x_search = None

from app.services.trips.incident_context import CandidateStopAssociation, CandidateStopContext
from app.services.trips.incident_evidence import (
    canonical_citation_url,
    source_identity_from_url,
    source_type_matches_url,
)
from app.utils.geo import _is_in_nyc

_MODEL_NAME = os.getenv("XAI_INCIDENT_MODEL", "grok-4-1-fast-reasoning")
_MAX_AGENT_TURNS = 2
_MAX_EVIDENCE_PER_INCIDENT = 6
_MAX_INCIDENTS = 12
_MAX_TEXT = {"location": 120, "description": 280, "source_origin": 160}
_SIX_HOURS = timedelta(hours=6)
_FUTURE_CLOCK_SKEW = timedelta(minutes=10)
_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_ALLOWED_SCOPES = {"nearby", "station_access", "subway_operations", "bus_corridor", "walking"}
_ALLOWED_SOURCE_TYPES = {"x_search", "web_search"}
_RAIL_MODES = {"subway", "rail", "train", "light_rail"}

def _bounded_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("XAI_INCIDENT_TIMEOUT_S", "12"))
    except ValueError:
        configured = 12.0
    return min(30.0, max(1.0, configured))


_XAI_REQUEST_TIMEOUT_S = _bounded_timeout_seconds()
client = (
    AsyncClient(api_key=os.getenv("XAI_API_KEY"), timeout=_XAI_REQUEST_TIMEOUT_S)
    if AsyncClient is not None and os.getenv("XAI_API_KEY")
    else None
)


GROK_INCIDENT_PROMPT = """You are SmartRoute's NYC transit incident researcher.
Search X and the web in parallel for current, public mobility conditions affecting
only the candidate route corridors below. Cover every listed station; do not invent
stations, geography, links, timestamps, or source identity.

Only report police/emergency activity, fires, shootings, closures, crashes,
infrastructure failures, station access constraints, or verified transit impacts.
Use neutral, non-graphic language. A street report affects a subway route only when
it explicitly affects station access, tracks, trains, service, or a listed route.
Do not make personal-safety claims or guarantees.

For every incident include the exact opaque `stop_ref` listed for the affected stop
and one or more evidence records. `nearby_station` is only the public display name;
it is never a substitute for `stop_ref`.
Each evidence URL must be an exact citation returned by a search tool. `source_origin`
must identify the original reporter/publisher (not an article merely repeating a post).
Use an offset-aware ISO-8601 observed timestamp from the source; omit evidence when it
lacks a current timestamp. If two pages repeat the same original source, list it once.

Return only JSON:
{{"incidents":[{{"location":"short public location","stop_ref":"exact listed opaque stop reference",
"nearby_station":"exact listed public station name",
"severity":"low|medium|high","description":"one concise evidence-grounded sentence",
"impact_scope":"nearby|station_access|subway_operations|bus_corridor|walking",
"evidence":[{{"source_type":"x_search|web_search","source_url":"exact citation URL",
"source_origin":"original account, outlet, or URL","observed_at":"ISO-8601"}}]}}]}}
Use {{"incidents":[]}} when no supported report exists.

Candidate route corridors:
{corridors}
"""


def _safe_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if text else ""


def _parse_observed_at(value: object, *, now: datetime) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    observed = parsed.astimezone(timezone.utc)
    if observed < now - _SIX_HOURS or observed > now + _FUTURE_CLOCK_SKEW:
        return None
    return observed.isoformat().replace("+00:00", "Z")


def _response_text(response: object) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    for item in content if isinstance(content, list) else []:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, Mapping):
            value = item.get("text")
            if isinstance(value, str):
                chunks.append(value)
        else:
            value = getattr(item, "text", None)
            if isinstance(value, str):
                chunks.append(value)
    return "".join(chunks)


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    try:
        parsed = json.loads(text.strip())
    except (TypeError, json.JSONDecodeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _citation_urls(response: object) -> set[str]:
    urls: set[str] = set()
    nested_keys = ("url", "href", "source_url", "web_citation", "x_citation")

    def collect(value: object) -> None:
        if value is None:
            return
        if isinstance(value, str):
            url = canonical_citation_url(value)
            if url:
                urls.add(url)
            return
        if isinstance(value, Mapping):
            for key in nested_keys:
                collect(value.get(key))
            return
        if isinstance(value, (bytes, int, float, bool)):
            return
        for key in nested_keys:
            collect(getattr(value, key, None))

    for citation in getattr(response, "citations", ()) or ():
        collect(citation)
    for citation in getattr(response, "inline_citations", ()) or ():
        collect(citation)
    return urls


def _completed_sources(response: object) -> set[str]:
    completed: set[str] = set()
    for call in getattr(response, "tool_calls", ()) or ():
        try:
            call_type = get_tool_call_type(call) if get_tool_call_type else ""
        except Exception:
            call_type = ""
        if call_type == "x_search_tool":
            completed.add("x_search")
        elif call_type == "web_search_tool":
            completed.add("web_search")
    usage = getattr(response, "server_side_tool_usage", ()) or ()
    for item in usage if isinstance(usage, (list, tuple, set)) else (usage,):
        text = str(item).casefold()
        if "x_search" in text:
            completed.add("x_search")
        if "web_search" in text:
            completed.add("web_search")
    return completed


def _context_is_in_nyc(context: CandidateStopContext) -> bool:
    return _is_in_nyc(context.latitude, context.longitude)


def _route_corridors(contexts: Iterable[CandidateStopContext]) -> str:
    corridors: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for context in contexts:
        if not context.stop_name or not _context_is_in_nyc(context):
            continue
        for association in context.associations:
            candidate = association.candidate_route_id
            segment_key = (association.route_id or "transit", association.segment_context or "")
            row = corridors.setdefault(candidate, {}).setdefault(
                segment_key,
                {
                    "route_id": association.route_id,
                    "mode": association.mode,
                    "segment": association.segment_context,
                    "stops": [],
                },
            )
            stop = {
                "stop_ref": context.stop_reference,
                "name": context.stop_name,
                "lat": round(context.latitude, 5),
                "lng": round(context.longitude, 5),
            }
            if stop not in row["stops"]:
                row["stops"].append(stop)
    compact = [
        {"candidate_route_id": candidate, "corridors": list(segments.values())}
        for candidate, segments in sorted(corridors.items())
    ]
    return json.dumps(compact, separators=(",", ":"))


def _contexts_by_stop_reference(route_context: Iterable[object]) -> dict[str, list[CandidateStopContext]]:
    result: dict[str, list[CandidateStopContext]] = {}
    for item in route_context:
        if not isinstance(item, CandidateStopContext) or not item.stop_name or not _context_is_in_nyc(item):
            continue
        result.setdefault(item.stop_reference, []).append(item)
    return result


def _affected_associations(
    contexts: Iterable[CandidateStopContext],
    scope: str,
) -> list[CandidateStopAssociation]:
    associations = [association for context in contexts for association in context.associations]
    if scope == "subway_operations":
        return [association for association in associations if association.mode in _RAIL_MODES]
    if scope == "bus_corridor":
        return [association for association in associations if association.mode == "bus"]
    if scope == "station_access":
        return associations
    if scope == "walking":
        return [association for association in associations if association.mode == "walk"]
    return []


def _normalized_evidence(
    raw: object,
    *,
    citations: set[str],
    now: datetime,
) -> list[dict[str, str]]:
    records = raw if isinstance(raw, list) else []
    evidence: list[dict[str, str]] = []
    seen_identities: set[str] = set()
    for item in records:
        if not isinstance(item, Mapping):
            continue
        source_type = _safe_text(item.get("source_type"), 24)
        source_url = canonical_citation_url(item.get("source_url"))
        origin = _safe_text(item.get("source_origin"), _MAX_TEXT["source_origin"])
        observed_at = _parse_observed_at(item.get("observed_at"), now=now)
        identity = source_identity_from_url(source_url)
        if (
            source_type not in _ALLOWED_SOURCE_TYPES
            or source_url is None
            or source_url not in citations
            or not origin
            or observed_at is None
            or identity is None
            or not source_type_matches_url(source_type, source_url)
        ):
            continue
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        evidence.append(
            {
                "source_type": source_type,
                "source_url": source_url,
                "source_origin": origin,
                "source_identity": identity,
                "observed_at": observed_at,
            }
        )
        if len(evidence) >= _MAX_EVIDENCE_PER_INCIDENT:
            break
    return evidence


def _normalize_incident_payload(
    payload: object,
    route_context: Iterable[CandidateStopContext],
    citations: set[str],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Accept only cited, fresh evidence tied to an exact candidate station."""
    raw_incidents = payload.get("incidents") if isinstance(payload, Mapping) else None
    if not isinstance(raw_incidents, list):
        return []
    contexts_by_reference = _contexts_by_stop_reference(route_context)
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized: list[dict[str, Any]] = []
    for item in raw_incidents[:_MAX_INCIDENTS]:
        if not isinstance(item, Mapping):
            continue
        reference = _safe_text(item.get("stop_ref"), 80)
        contexts = contexts_by_reference.get(reference, [])
        if not contexts:
            continue
        location = _safe_text(item.get("location"), _MAX_TEXT["location"])
        description = _safe_text(item.get("description"), _MAX_TEXT["description"])
        severity = _safe_text(item.get("severity"), 16).lower()
        scope = _safe_text(item.get("impact_scope"), 40).lower()
        evidence = _normalized_evidence(item.get("evidence"), citations=citations, now=observed_now)
        if not location or not description or severity not in _ALLOWED_SEVERITIES or scope not in _ALLOWED_SCOPES or not evidence:
            continue
        associations = _affected_associations(contexts, scope)
        candidate_ids = sorted({association.candidate_route_id for association in associations})
        modes = sorted({association.mode for association in associations if association.mode})
        origins = {
            canonical_citation_url(entry["source_origin"]) or entry["source_origin"].casefold()
            for entry in evidence
        }
        identities = {entry["source_identity"] for entry in evidence}
        source_types = sorted({entry["source_type"] for entry in evidence})
        route_impacting = scope != "nearby" and bool(candidate_ids)
        corroborated = len(identities) >= 2
        independently_corroborated = (
            corroborated
            and set(source_types) == _ALLOWED_SOURCE_TYPES
            and len(origins) == len(evidence)
        )
        normalized.append(
            {
                "location": location,
                "nearby_station": contexts[0].stop_name,
                "severity": severity,
                "description": description,
                "source": " + ".join(source_types),
                "impact_scope": scope,
                "affected_candidate_route_ids": candidate_ids,
                "affected_modes": modes,
                "evidence": evidence,
                "corroborated": corroborated,
                "advisor_eligible": independently_corroborated and route_impacting,
            }
        )
    return normalized


def _metadata(*, status: str, completed: Iterable[str] = (), error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "sources": {
            "attempted": ["x_search", "web_search"],
            "completed": sorted(set(completed)),
        },
        "tool_rounds": 0,
    }
    if error:
        payload["sources"]["errors"] = [error]
    return payload


async def _run_incident_agent(contexts: list[CandidateStopContext]) -> dict[str, Any]:
    if client is None or not all((system, user, x_search, web_search)):
        return {"incidents": [], "scan_metadata": _metadata(status="disabled", error="xAI incident scanning is not configured")}
    corridors = _route_corridors(contexts)
    if not corridors or corridors == "[]":
        return {"incidents": [], "scan_metadata": _metadata(status="failed", error="candidate stops unavailable")}
    now = datetime.now(timezone.utc)
    try:
        chat = client.chat.create(
            model=_MODEL_NAME,
            tools=[
                x_search(from_date=now - _SIX_HOURS, to_date=now),
                web_search(
                    user_location_country="US",
                    user_location_city="New York",
                    user_location_region="NY",
                    user_location_timezone="America/New_York",
                ),
            ],
            temperature=0.0,
            parallel_tool_calls=True,
            max_turns=_MAX_AGENT_TURNS,
            response_format="json_object",
            include=["inline_citations"],
        )
        chat.append(system(GROK_INCIDENT_PROMPT.format(corridors=corridors)))
        chat.append(user("Research both sources for the supplied corridors and return the required JSON."))
    except Exception:
        return {"incidents": [], "scan_metadata": _metadata(status="failed", error="xAI scan initialization failed")}

    # With server-side tools, xAI performs up to ``max_turns`` internally in
    # one request. Calling ``sample`` again would begin another two-turn agent
    # run and violate the production search budget.
    try:
        response = await chat.sample()
    except asyncio.CancelledError:
        raise
    except Exception:
        return {"incidents": [], "scan_metadata": _metadata(status="failed", error="xAI sampling failed")}
    completed = _completed_sources(response)
    citations = _citation_urls(response)
    payload = _parse_json_object(_response_text(response))
    if payload is None:
        return {
            "incidents": [],
            "scan_metadata": _metadata(
                status="failed",
                completed=completed,
                error="xAI returned no incident JSON",
            ),
        }
    status = "complete" if completed == _ALLOWED_SOURCE_TYPES else "partial"
    metadata = _metadata(status=status, completed=completed)
    metadata["tool_rounds"] = 1
    return {
        "incidents": _normalize_incident_payload(payload, contexts, citations, now=now),
        "scan_metadata": metadata,
    }


async def get_incidents(route_context: Iterable[object]) -> dict[str, Any]:
    """Return cited incident evidence for candidate stops without route selection."""
    contexts = [item for item in route_context if isinstance(item, CandidateStopContext)]
    if not contexts:
        return {"incidents": [], "scan_metadata": _metadata(status="failed", error="candidate stops unavailable")}
    return await _run_incident_agent(contexts)


async def close_incident_client() -> None:
    """Release the async gRPC transport during application shutdown."""
    global client
    active = client
    client = None
    if active is not None:
        await active.close()
