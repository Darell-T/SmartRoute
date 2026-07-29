"""Bounded Grok incident scan over route candidates and a cached 511NY snapshot.

This module never instantiates a 511NY client or fetches 511NY at request time.
``main.lifespan`` owns the optional, process-local poller and supplies its
snapshot store.  That is intentionally a single-process deployment design;
multiple web workers need a shared snapshot service before this is enabled in
each worker.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Iterable, Mapping

try:
    from xai_sdk import Client
    from xai_sdk.chat import system, tool, tool_result, user
    from xai_sdk.tools import get_tool_call_type, web_search, x_search
except Exception:  # Optional integration: trip planning must remain available.
    Client = None
    system = tool = tool_result = user = None
    get_tool_call_type = web_search = x_search = None

from app.services.trips.incident_context import CandidateStopContext
from app.services.trips.incident_association import (
    attach_verified_match_association,
    normalize_matcher_association,
)
from app.services.trips.incident_matching import Cached511NYSearchTool


_ALLOWED_SEVERITIES = {"low", "medium", "high"}
_MODEL_NAME = "grok-4-1-fast-reasoning"
_XAI_API_KEY = os.getenv("XAI_API_KEY")
_LOCAL_TOOL_NAME = "search_cached_511ny_incidents"
_MAX_TOOL_ROUNDS = 4
_MAX_LOCAL_TOOL_CALLS = 3
_MAX_TOTAL_TOOL_CALLS = 6
_MAX_LOCAL_STOPS = 500
_MAX_LOCAL_SNAPSHOT_INCIDENTS = 500
_MAX_PROMPT_STOPS = 80
_OFFICIAL_RAW_FIELDS = (
    "latitude",
    "longitude",
    "reported_at",
    "updated_at",
    "starts_at",
    "expected_end_at",
    "event_type",
    "event_subtype",
    "roadway_name",
    "status",
    "status_text",
)
_STATION_TOKEN_MAP = {
    "av": "avenue", "ave": "avenue", "blvd": "boulevard", "bklyn": "brooklyn",
    "ctr": "center", "ct": "center", "hwy": "highway", "pkwy": "parkway",
    "pl": "place", "plz": "plaza", "rd": "road", "sq": "square", "st": "street",
}

def _bounded_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("XAI_INCIDENT_TIMEOUT_S", "12"))
    except ValueError:
        configured = 12.0
    return min(30.0, max(1.0, configured))


_XAI_REQUEST_TIMEOUT_S = _bounded_timeout_seconds()
client = Client(api_key=_XAI_API_KEY, timeout=_XAI_REQUEST_TIMEOUT_S) if Client is not None and _XAI_API_KEY else None
_snapshot_store: Any = None

GROK_INCIDENT_PROMPT = """You are SmartRoute's NYC incident scanner. Assess only the
current route candidates listed below, using current evidence from X and the web plus
the supplied cached official 511NY search tool.

Candidate stations: {stations}
Candidate route context (use only these candidate_route_ids in the local tool):
{candidate_context}

Classify evidence precisely:
- Roadway incidents may affect a bus corridor, walking approach, station access, or
  street safety; do not call them a subway-service disruption without evidence.
- Bus incidents affect a bus route/corridor only when evidence supports that.
- Walk/station-access issues affect access, not train operations unless evidence says so.
- Subway operational impact requires evidence about service, stations, tracks, or trains.

Before concluding there are no incidents, call X search and web search. When the
cached 511NY tool is available, call it once as well. For every selected 511NY
result, include its exact `source_ref` from the local tool in that incident; do
not invent source_ref values. Do not suppress a relevant selected 511NY result
merely because social search is empty. Only include specific,
current, NYC incidents relevant to a listed station/candidate.
Do not invent links, keys, URLs, neighborhoods, or geography. If evidence is unclear,
omit it. Return ONLY this JSON object (no markdown):
{{"incidents":[{{"location":"address or cross-street","nearby_station":"exact listed station name","severity":"low|medium|high","description":"one concise, evidence-grounded sentence","source":"source name or @handle","source_ref":"optional exact selected 511NY source_id"}}]}}
Use {{"incidents":[]}} when no report is supported."""


def configure_snapshot_store(store: Any | None) -> None:
    """Set the lifecycle-owned, process-local snapshot source (or clear it)."""
    global _snapshot_store
    _snapshot_store = store


def _normalize_text_field(value: Any) -> str:
    return " ".join(value.split()).strip() if isinstance(value, str) else ""


def _station_key(value: Any) -> str:
    text = _normalize_text_field(value).casefold()
    tokens = text.translate(str.maketrans({"&": " ", ",": " ", "-": " ", "/": " "})).replace(".", " ").split()
    return " ".join(_STATION_TOKEN_MAP.get(token, token) for token in tokens if token)


def _normalize_station_names(route_stops: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for stop in route_stops or []:
        name = _normalize_text_field(stop)
        key = _station_key(name)
        if key and key not in seen:
            seen.add(key)
            normalized.append(name)
    return normalized


def _station_names(route_context: Iterable[Any]) -> list[str]:
    names: list[Any] = []
    for item in route_context or []:
        if isinstance(item, CandidateStopContext):
            names.append(item.stop_name)
        elif isinstance(item, Mapping):
            names.append(item.get("stop_name") or item.get("name"))
        else:
            names.append(item)
    return _normalize_station_names(names)


def _stop_contexts(route_context: Iterable[Any]) -> list[CandidateStopContext]:
    return [item for item in route_context or [] if isinstance(item, CandidateStopContext)]


def _compact_candidate_context(stops: Iterable[CandidateStopContext]) -> str:
    """Bounded IDs/associations only; coordinates and arbitrary input stay local."""
    stop_list = list(stops)
    candidate_route_ids = list(dict.fromkeys(
        candidate_id
        for stop in stop_list
        for candidate_id in stop.candidate_route_ids
    ))[:12]
    rows = []
    for stop in stop_list[:_MAX_PROMPT_STOPS]:
        associations = [
            {
                "candidate_route_id": association.candidate_route_id,
                "mode": association.mode,
                "route_id": association.route_id,
            }
            for association in stop.associations[:8]
        ]
        rows.append({
            "stop": _normalize_text_field(stop.stop_name)[:80],
            "candidate_route_ids": stop.candidate_route_ids[:12],
            "associations": associations,
        })
    return json.dumps({"candidate_route_ids": candidate_route_ids, "stops": rows}, separators=(",", ":"))


def _strip_code_fences(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    return text[:-3].strip() if text.endswith("```") else text.strip()


def _response_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    for item in content if isinstance(content, list) else []:
        chunks.append(item if isinstance(item, str) else item.get("text", "") if isinstance(item, dict) else getattr(item, "text", ""))
    return "".join(chunk for chunk in chunks if isinstance(chunk, str))


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = _strip_code_fences(content)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_incident(item: Any) -> dict[str, str] | None:
    if not isinstance(item, Mapping):
        return None
    result = {key: _normalize_text_field(item.get(key)) for key in ("location", "nearby_station", "severity", "description", "source")}
    result["severity"] = result["severity"].lower()
    return result if all(result.values()) and result["severity"] in _ALLOWED_SEVERITIES else None


def _selected_511_evidence(raw: Mapping[str, Any], match: Mapping[str, Any]) -> dict[str, Any]:
    """Join raw normalized 511NY fields to its exact local matcher result.

    The raw row remains authoritative for provider values (coordinates and
    lifecycle timestamps).  Candidate linkage is taken exclusively from the
    local match result, which was scoped to current candidate stops.
    """
    selected: dict[str, Any] = {}
    source_id = _normalize_text_field(raw.get("source_id") or match.get("source_id"))
    if source_id:
        selected["source_id"] = source_id[:120]
    source = _normalize_text_field(raw.get("source") or match.get("source"))
    if source:
        selected["source"] = source[:60]
    for key in _OFFICIAL_RAW_FIELDS:
        if raw.get(key) is not None:
            selected[key] = raw[key]
    return attach_verified_match_association(selected, match)


def _normalize_incident_payload(
    payload: Any,
    allowed_stations: list[str] | None = None,
    *,
    selected_511_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    incidents = payload.get("incidents") if isinstance(payload, Mapping) else None
    if not isinstance(incidents, list):
        return {"incidents": []}
    station_map = {_station_key(station): station for station in allowed_stations or [] if station}
    normalized: list[dict[str, Any]] = []
    for item in incidents:
        incident = _normalize_incident(item)
        if incident is None:
            continue
        if station_map:
            canonical = station_map.get(_station_key(incident["nearby_station"]))
            if not canonical:
                continue
            incident["nearby_station"] = canonical
        source_ref = item.get("source_ref")
        official = selected_511_evidence.get(source_ref) if isinstance(source_ref, str) and selected_511_evidence else None
        if official:
            incident["source_id"] = str(official.get("source_id") or source_ref)
            incident["sources"] = list(dict.fromkeys([incident["source"], str(official.get("source") or "511ny")]))
            for key in _OFFICIAL_RAW_FIELDS:
                if official.get(key) is not None:
                    incident[key] = official[key]
            # These values are copied only from an exact local-tool match
            # keyed by source_ref. Model-provided association keys were
            # discarded by _normalize_incident above and cannot reach this
            # branch. A raw snapshot row alone is not enough to assert route
            # relevance: it must be paired with the matcher provenance bit.
            if official.get("_verified_511ny_match") is True:
                incident.update(normalize_matcher_association(official))
                incident["_verified_511ny_match"] = True
        normalized.append(incident)
    return {"incidents": normalized}


def _metadata(snapshot: Any, *, status: str = "complete") -> dict[str, Any]:
    snapshot_mapping = snapshot.model_dump() if hasattr(snapshot, "model_dump") else snapshot if isinstance(snapshot, Mapping) else {}
    snapshot_status = str(snapshot_mapping.get("status") or "disabled")
    return {
        "status": status,
        "snapshot_status": snapshot_status,
        "sources": {"attempted": ["x_search", "web_search", "cached_511ny"], "completed": [], "errors": []},
        "tool_rounds": 0,
        "local_tool_calls": 0,
        "total_tool_calls": 0,
    }


def _safe_tool_error(reason: str) -> str:
    return reason[:120].replace("\n", " ")


def _tool_call_arguments(call: Any) -> tuple[str, dict[str, Any] | None]:
    function = getattr(call, "function", None)
    raw = getattr(function, "arguments", "")
    if not isinstance(raw, str):
        return "invalid JSON arguments", None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "invalid JSON arguments", None
    return ("", value) if isinstance(value, dict) else ("arguments must be a JSON object", None)


def _required_sources(snapshot_status: str) -> set[str]:
    required = {"x_search", "web_search"}
    if snapshot_status in {"fresh", "stale"}:
        required.add("cached_511ny")
    return required


def _finalize_metadata(metadata: dict[str, Any], *, has_final_json: bool = False) -> None:
    """Only an evidenced scan can be complete; empty never means all-clear alone."""
    completed = set(metadata["sources"]["completed"])
    required = _required_sources(metadata["snapshot_status"])
    if not has_final_json:
        metadata["status"] = "partial" if completed else "failed"
    elif metadata["snapshot_status"] == "fresh" and required.issubset(completed) and not metadata["sources"]["errors"]:
        metadata["status"] = "complete"
    else:
        metadata["status"] = "partial"


def _bounded_local_snapshot(snapshot: Any) -> tuple[Any, bool]:
    """Cap deterministic local matching input without touching the shared snapshot."""
    mapping = snapshot.model_dump() if hasattr(snapshot, "model_dump") else snapshot if isinstance(snapshot, Mapping) else None
    if not isinstance(mapping, Mapping) or not isinstance(mapping.get("incidents"), list):
        return snapshot, False
    records = mapping["incidents"]
    if len(records) <= _MAX_LOCAL_SNAPSHOT_INCIDENTS:
        return snapshot, False
    bounded = dict(mapping)
    bounded["incidents"] = records[:_MAX_LOCAL_SNAPSHOT_INCIDENTS]
    return bounded, True


def _snapshot_evidence_by_id(snapshot: Any) -> dict[str, Mapping[str, Any]]:
    mapping = snapshot.model_dump() if hasattr(snapshot, "model_dump") else snapshot if isinstance(snapshot, Mapping) else {}
    records = mapping.get("incidents", []) if isinstance(mapping, Mapping) else []
    evidence: dict[str, Mapping[str, Any]] = {}
    for record in records if isinstance(records, list) else []:
        row = record.model_dump() if hasattr(record, "model_dump") else record
        if isinstance(row, Mapping) and isinstance(row.get("source_id"), str):
            evidence[row["source_id"]] = dict(row)
    return evidence


def _run_incident_agent(
    station_names: str,
    station_list: list[str],
    snapshot: Any = None,
    stop_contexts: Iterable[CandidateStopContext] = (),
) -> dict[str, Any]:
    metadata = _metadata(snapshot)
    if not client or not all((system, user, tool, tool_result, x_search, web_search, get_tool_call_type)) or not _XAI_API_KEY:
        metadata["status"] = "disabled"
        metadata["sources"]["errors"].append("xAI incident scanning is not configured")
        return {"incidents": [], "scan_metadata": metadata}

    bounded_snapshot, snapshot_capped = _bounded_local_snapshot(snapshot)
    all_stops = list(stop_contexts)
    bounded_stops = all_stops[:_MAX_LOCAL_STOPS]
    if snapshot_capped or len(all_stops) > _MAX_LOCAL_STOPS:
        metadata["sources"]["errors"].append("cached 511NY matching input was capped")
    local_search = Cached511NYSearchTool(lambda: bounded_snapshot, bounded_stops)
    snapshot_evidence = _snapshot_evidence_by_id(bounded_snapshot)
    selected_511_evidence: dict[str, Mapping[str, Any]] = {}
    candidate_context = _compact_candidate_context(bounded_stops)
    try:
        chat = client.chat.create(
            model=_MODEL_NAME,
            tools=[
                x_search(),
                web_search(),
                tool(_LOCAL_TOOL_NAME, local_search.schema["description"], local_search.schema["input_schema"]),
            ],
            temperature=0.0,
            parallel_tool_calls=False,
            max_turns=_MAX_TOOL_ROUNDS,
        )
        chat.append(system(GROK_INCIDENT_PROMPT.format(stations=station_names, candidate_context=candidate_context)))
        chat.append(user(f"Check current evidence only for these route stations and candidate IDs: {candidate_context}"))
    except Exception:
        metadata["status"] = "failed"
        metadata["sources"]["errors"].append("xAI scan initialization failed")
        return {"incidents": [], "scan_metadata": metadata}

    seen_calls: set[str] = set()
    seen_tool_call_ids: set[str] = set()
    for _ in range(_MAX_TOOL_ROUNDS):
        try:
            response = chat.sample()
        except Exception:
            metadata["status"] = "failed"
            metadata["sources"]["errors"].append("xAI sampling failed")
            return {"incidents": [], "scan_metadata": metadata}
        calls = list(getattr(response, "tool_calls", []) or [])
        if not calls:
            payload = _parse_json_object(_response_text(response))
            if payload is None:
                metadata["status"] = "failed"
                metadata["sources"]["errors"].append("xAI returned malformed incident JSON")
                return {"incidents": [], "scan_metadata": metadata}
            result = _normalize_incident_payload(payload, station_list, selected_511_evidence=selected_511_evidence)
            _finalize_metadata(metadata, has_final_json=True)
            return {**result, "scan_metadata": metadata}

        metadata["tool_rounds"] += 1
        if metadata["total_tool_calls"] + len(calls) > _MAX_TOTAL_TOOL_CALLS:
            metadata["sources"]["errors"].append("tool call batch exceeds total limit")
            _finalize_metadata(metadata)
            return {"incidents": [], "scan_metadata": metadata}
        chat.append(response)
        for call in calls:
            metadata["total_tool_calls"] += 1
            try:
                call_type = get_tool_call_type(call)
            except Exception:
                metadata["sources"]["errors"].append("unknown tool call type")
                continue
            if call_type in {"x_search_tool", "web_search_tool"}:
                name = "x_search" if call_type == "x_search_tool" else "web_search"
                if name not in metadata["sources"]["completed"]:
                    metadata["sources"]["completed"].append(name)
                continue  # Server-side built-ins are executed by xAI, never locally.
            if call_type != "client_side_tool":
                metadata["sources"]["errors"].append("unsupported tool call type")
                continue
            metadata["local_tool_calls"] += 1
            function = getattr(call, "function", None)
            name = getattr(function, "name", "")
            error, arguments = _tool_call_arguments(call)
            call_id = getattr(call, "id", None)
            valid_call_id = isinstance(call_id, str) and bool(call_id.strip()) and call_id not in seen_tool_call_ids
            fingerprint = json.dumps({"name": name, "arguments": arguments}, sort_keys=True, separators=(",", ":")) if arguments is not None else f"{name}:invalid"
            if not isinstance(call_id, str) or not call_id.strip():
                error = "missing tool call id"
            elif call_id in seen_tool_call_ids:
                error = "duplicate tool call id"
            elif name != _LOCAL_TOOL_NAME:
                error = "unrecognized client-side tool"
            elif metadata["local_tool_calls"] > _MAX_LOCAL_TOOL_CALLS:
                error = "local tool call limit reached"
            elif fingerprint in seen_calls:
                error = "duplicate local tool call"
            if valid_call_id:
                seen_tool_call_ids.add(call_id)
            if error:
                metadata["sources"]["errors"].append(_safe_tool_error(error))
                result = {"incidents": [], "status": "invalid_arguments", "error": "request rejected"}
            else:
                seen_calls.add(fingerprint)
                result = local_search.execute(arguments or {})
                if result.get("status") != "complete":
                    metadata["sources"]["errors"].append("cached 511NY search was unavailable or invalid")
                elif "cached_511ny" not in metadata["sources"]["completed"]:
                    metadata["sources"]["completed"].append("cached_511ny")
                for match in result.get("incidents", []) if isinstance(result.get("incidents"), list) else []:
                    source_id = match.get("source_id") if isinstance(match, Mapping) else None
                    if isinstance(source_id, str) and source_id in snapshot_evidence:
                        selected_511_evidence[source_id] = _selected_511_evidence(snapshot_evidence[source_id], match)
            if not valid_call_id:
                continue
            chat.append(tool_result(json.dumps(result, separators=(",", ":")), tool_call_id=call_id))

    _finalize_metadata(metadata)
    metadata["sources"]["errors"].append("xAI tool round limit reached")
    return {"incidents": [], "scan_metadata": metadata}


async def _snapshot_before_scan(snapshot_store: Any | None) -> Any:
    store = snapshot_store if snapshot_store is not None else _snapshot_store
    if store is None:
        return {"incidents": [], "status": "disabled"}
    try:
        return await store.get_snapshot()
    except Exception:
        # Never turn a cache-read failure into an upstream fetch or all-clear.
        return {"incidents": [], "status": "unavailable", "last_error": "snapshot unavailable"}


async def get_incidents(route_context: Iterable[Any], *, snapshot_store: Any | None = None) -> dict[str, Any]:
    """Scan using one previously-read snapshot; accepts legacy station name lists."""
    route_context = list(route_context or [])
    station_list = _station_names(route_context)
    snapshot = await _snapshot_before_scan(snapshot_store)
    if not station_list:
        metadata = _metadata(snapshot, status="complete")
        return {"incidents": [], "scan_metadata": metadata}
    try:
        return await asyncio.to_thread(
            _run_incident_agent, ", ".join(station_list), station_list, snapshot, _stop_contexts(route_context)
        )
    except Exception:
        metadata = _metadata(snapshot, status="failed")
        metadata["sources"]["errors"].append("xAI incident scan failed")
        print("[incident_monitor] Grok agent failed")
        return {"incidents": [], "scan_metadata": metadata}
