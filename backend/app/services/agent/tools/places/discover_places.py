"""Public discover_places capability over the Google Places boundary."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services import geography as geo
from app.services.agent import discovery_store
from app.services.agent import trip_state as trip_state_module
from app.services.agent.tools._types import ToolContext, ToolOutcome, ToolResult
from app.services.agent.tools.places import damn_lines, search_local_places
from app.services.agent.tools.places import geography as conversational_geography
from app.services.agent.turn.contract import GoalKind

_DISCOVERY_GOAL_KINDS = frozenset(
    {GoalKind.PLACE_RECOMMENDATION, GoalKind.DESTINATION_SELECTION, GoalKind.ROUTE}
)
_LOGGER = logging.getLogger(__name__)
_MISSING = object()


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    operation: str
    query: str
    scope: dict[str, Any]
    names: list[str]
    max_results: int
    open_now: bool | None
    exclude_presented: bool
    queue_context: dict[str, Any]
    session_id: str


def validate_request(
    tool_input: dict, ctx: ToolContext
) -> DiscoveryRequest | ToolResult:
    session_id = str(getattr(ctx, "session_id", None) or "").strip()
    if not session_id:
        return ToolResult(ok=False, error="session is required for local discovery")

    operation = _operation(tool_input.get("operation"))
    if operation is None:
        return ToolResult(
            ok=False,
            error="operation must be search or verify",
            internal_diagnostic=True,
        )

    query = str(tool_input.get("query") or "").strip()
    if not query:
        return ToolResult(ok=False, error="query is required", internal_diagnostic=True)

    scope = _validated_scope(tool_input.get("scope"), ctx)
    if isinstance(scope, ToolResult):
        return scope
    names = _validated_candidate_names(operation, tool_input.get("candidate_names"))
    if isinstance(names, ToolResult):
        return names
    open_now = _validated_open_now(tool_input.get("open_now"))
    if isinstance(open_now, ToolResult):
        return open_now
    exclude_presented = _validated_exclude_presented(
        operation, tool_input.get("exclude_presented")
    )
    if isinstance(exclude_presented, ToolResult):
        return exclude_presented
    queue_context = _validated_queue_context(tool_input.get("queue_context", _MISSING))
    if isinstance(queue_context, ToolResult):
        return queue_context
    return DiscoveryRequest(
        operation=operation,
        query=query,
        scope=scope,
        names=names,
        max_results=_clamp_max_results(tool_input.get("max_results")),
        open_now=open_now,
        exclude_presented=exclude_presented,
        queue_context=queue_context,
        session_id=session_id,
    )


def _validated_scope(value: object, ctx: ToolContext) -> dict[str, Any] | ToolResult:
    scope, scope_error = conversational_geography.normalize_scope(value)
    if scope is None or scope_error:
        return ToolResult(
            ok=False,
            error=scope_error or "scope is invalid",
            internal_diagnostic=True,
        )
    if scope["kind"] == "current_location" and not _has_authoritative_location(ctx):
        return ToolResult(
            ok=False,
            error="current location is unavailable; share GPS or name an area",
        )
    return scope


def _validated_candidate_names(
    operation: str,
    value: object,
) -> list[str] | ToolResult:
    names = _candidate_names(value)
    if operation == "search" and names:
        return ToolResult(
            ok=False,
            error="search requires an empty candidate_names array",
            internal_diagnostic=True,
        )
    if operation == "verify" and not 1 <= len(names) <= 5:
        return ToolResult(
            ok=False,
            error="verify requires one through five candidate names",
            internal_diagnostic=True,
        )
    return names


def _validated_open_now(value: object) -> bool | ToolResult | None:
    if value is not None and not isinstance(value, bool):
        return ToolResult(
            ok=False,
            error="open_now must be boolean or null",
            internal_diagnostic=True,
        )
    return value


def _validated_exclude_presented(
    operation: str,
    value: object,
) -> bool | ToolResult:
    if value is None:
        return False
    if not isinstance(value, bool):
        return ToolResult(
            ok=False,
            error="exclude_presented must be boolean",
            internal_diagnostic=True,
        )
    if value and operation != "search":
        return ToolResult(
            ok=False,
            error="exclude_presented is valid only for search",
            internal_diagnostic=True,
        )
    return value


def _validated_queue_context(value: object) -> dict[str, Any] | ToolResult:
    if value is _MISSING:
        return {"mode": "ignore", "max_wait_minutes": None}
    context = discovery_store.sanitized_queue_context(value)
    if context is None:
        return ToolResult(
            ok=False,
            error=(
                "queue_context requires only a valid mode and a non-negative "
                "finite max_wait_minutes number or null"
            ),
            internal_diagnostic=True,
        )
    return context


def _operation(value: object) -> str | None:
    operation = str(value or "").strip().casefold()
    return operation if operation in {"search", "verify"} else None


def _clamp_max_results(value: object) -> int:
    try:
        parsed = int(value) if value is not None else 8
    except (TypeError, ValueError):
        parsed = 8
    return max(1, min(8, parsed))


def _candidate_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = " ".join(str(item or "").split())[:120]
        key = name.casefold()
        if name and key not in seen:
            names.append(name)
            seen.add(key)
        if len(names) >= 5:
            break
    return names


def _has_authoritative_location(ctx: ToolContext) -> bool:
    origin = ctx.origin if isinstance(ctx.origin, dict) else {}
    return origin.get("lat") is not None and origin.get("lng") is not None


DISCOVER_PLACES_SCHEMA = {
    "name": "discover_places",
    "description": "Search or verify NYC places in a server-owned discovery set.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["search", "verify"],
                "description": "search for a category/query, or verify exact names.",
            },
            "query": {
                "type": "string",
                "description": "What to find, e.g. pizza. For verify, a display query.",
            },
            "scope": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["current_location", "boroughs", "nyc", "named_area"],
                    },
                    "values": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "values"],
                "additionalProperties": False,
                "description": "Rider-authorized geography chosen for this search.",
            },
            "open_now": {
                "type": ["boolean", "null"],
                "description": "When true, keep only currently open places.",
            },
            "max_results": {
                "type": "integer",
                "description": "Requested cap; the server clamps to 1-8.",
            },
            "candidate_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact names to verify. Empty for search.",
            },
            "exclude_presented": {
                "type": "boolean",
                "description": "For search only, omit places already presented in this session.",
            },
            "queue_context": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["ignore", "heads_up", "decision", "historical"],
                    },
                    "max_wait_minutes": {"type": ["number", "null"]},
                },
                "required": ["mode", "max_wait_minutes"],
                "additionalProperties": False,
                "description": "Queue relevance for only this destination decision.",
            },
            "goal_key": {
                "type": "string",
                "description": "Turn goal associated with this discovery request.",
            },
            "activity_label": {
                "type": ["string", "null"],
                "description": "Optional progress phrase; use null for simple actions.",
            },
        },
        "required": [
            "operation",
            "query",
            "scope",
            "open_now",
            "max_results",
            "candidate_names",
            "exclude_presented",
            "queue_context",
            "goal_key",
            "activity_label",
        ],
        "additionalProperties": False,
    },
}


def _validate_goal_key(
    tool_input: dict, ctx: ToolContext
) -> tuple[str | None, ToolResult | None]:
    evidence = getattr(ctx, "turn_evidence", None)
    contract = getattr(evidence, "turn_contract", None)
    if contract is None:
        return None, None
    raw_goal_key = tool_input.get("goal_key")
    if not isinstance(raw_goal_key, str) or not raw_goal_key.strip():
        return None, ToolResult(
            ok=False,
            error="goal_key is required when a turn contract is active",
            internal_diagnostic=True,
        )
    goal_key = raw_goal_key.strip()
    goal = contract.get_goal(goal_key)
    if goal is None:
        return None, ToolResult(
            ok=False,
            error="goal_key is unknown for this turn contract",
            internal_diagnostic=True,
        )
    if goal.kind not in _DISCOVERY_GOAL_KINDS or (
        goal.kind == GoalKind.ROUTE
        and not contract.route_allows_internal_discovery(goal_key)
    ):
        return None, ToolResult(
            ok=False,
            error="goal_key is incompatible with discover_places",
            internal_diagnostic=True,
        )
    return goal_key, None


async def execute(tool_input: dict, ctx: ToolContext) -> ToolResult:
    _goal_key, goal_error = _validate_goal_key(tool_input, ctx)
    if goal_error:
        return goal_error
    request = validate_request(tool_input, ctx)
    if isinstance(request, ToolResult):
        return request
    if request.operation == "verify":
        return await _verify(
            names=request.names,
            query=request.query,
            scope=request.scope,
            open_now=request.open_now,
            max_results=request.max_results,
            ctx=ctx,
            session_id=request.session_id,
            queue_context=request.queue_context,
        )
    return await _search(
        query=request.query,
        scope=request.scope,
        open_now=request.open_now,
        max_results=request.max_results,
        exclude_presented=request.exclude_presented,
        ctx=ctx,
        session_id=request.session_id,
        queue_context=request.queue_context,
    )


async def _search(
    *,
    query: str,
    scope: dict[str, Any],
    open_now: bool | None,
    max_results: int,
    exclude_presented: bool,
    ctx: ToolContext,
    session_id: str,
    queue_context: dict[str, Any],
) -> ToolResult:
    targets = search_local_places._search_targets(scope)
    prior_tokens = (
        _matching_continuation_tokens(
            ctx=ctx,
            session_id=session_id,
            query=query,
            scope=scope,
        )
        if exclude_presented
        else {}
    )
    per_target = max(1, math.ceil(max_results / max(1, len(targets))))
    if scope["kind"] == "current_location" or exclude_presented:
        # Fetch the bounded local pool before applying the rider-facing cap so
        # distance can narrow the candidate pool without choosing a route.
        per_target = max(per_target, discovery_store.MAX_PLACES)
    requests: list[Awaitable[ToolResult]] = []
    for index, target in enumerate(targets):
        provider_request = {
            "query": query,
            "near": target["near"],
            "max_results": per_target,
            "restrict_to_area": scope["kind"] == "named_area",
        }
        token = prior_tokens.get(_target_key(index))
        if token:
            provider_request["page_token"] = token
        requests.append(search_local_places._provider_search(provider_request, ctx))
    results = await _provider_results(requests)
    if not any(result.ok for result in results):
        return ToolResult(
            ok=False,
            error="current place search was unavailable for one or more requested areas",
            timings=search_local_places._merged_timings(results),
        )
    source = _interleaved_sources(targets, results, scope)
    coverage = search_local_places._coverage(targets, results)
    if scope["kind"] in {"boroughs", "nyc"}:
        missing: list[str] = []
        for target, result in zip(targets, results, strict=False):
            if not result.ok:
                continue
            has_match = any(
                search_local_places._target_accepts_place(place, target, scope)
                for place in search_local_places._provider_places(result)
            )
            if not has_match:
                missing.append(str(target.get("label") or ""))
        if missing:
            unavailable = list(
                dict.fromkeys([*(coverage.get("unavailable_areas") or []), *missing])
            )
            coverage.update(status="partial", unavailable_areas=unavailable)
    return await _persist(
        source_places=source,
        query=query,
        scope=scope,
        open_now=open_now,
        max_results=max_results,
        ctx=ctx,
        session_id=session_id,
        timings=search_local_places._merged_timings(results),
        unverified_names=[],
        operation="search",
        requested_count=max_results,
        coverage=coverage,
        exclude_presented=exclude_presented,
        queue_context=queue_context,
        continuation_tokens=_response_continuation_tokens(results),
    )


async def _verify(
    *,
    names: list[str],
    query: str,
    scope: dict[str, Any],
    open_now: bool | None,
    max_results: int,
    ctx: ToolContext,
    session_id: str,
    queue_context: dict[str, Any],
) -> ToolResult:
    targets = search_local_places._search_targets(scope) or [
        {"near": None, "label": ""}
    ]
    searches = []
    for name in names:
        for target in targets:
            pending = search_local_places._provider_search(
                {
                    "query": name,
                    "near": target["near"],
                    "max_results": 3,
                    "restrict_to_area": scope["kind"] == "named_area",
                },
                ctx,
            )
            searches.append((name, target, pending))
    results = await _provider_results([item[2] for item in searches])
    if not any(result.ok for result in results):
        return ToolResult(
            ok=False,
            error="place verification was unavailable",
            timings=search_local_places._merged_timings(results),
        )
    source: list[tuple[dict, str]] = []
    unverified: list[str] = []
    by_name: dict[str, tuple[dict, str] | None] = dict.fromkeys(names)
    for (name, target, _pending), result in zip(searches, results, strict=False):
        if not result.ok:
            continue
        if by_name[name] is not None:
            continue
        matches = [
            place
            for place in search_local_places._provider_places(result)
            if search_local_places._target_accepts_place(place, target, scope)
            and _name_matches(name, place)
        ]
        if matches:
            by_name[name] = (matches[0], str(target.get("label") or ""))
    for name in names:
        match = by_name[name]
        if match is None:
            unverified.append(name)
            continue
        source.append(match)
    return await _persist(
        source_places=source,
        query=query or names[0],
        scope=scope,
        open_now=open_now,
        max_results=max_results,
        ctx=ctx,
        session_id=session_id,
        timings=search_local_places._merged_timings(results),
        unverified_names=unverified,
        operation="verify",
        requested_count=max_results,
        coverage=search_local_places._coverage(
            [item[1] for item in searches],
            results,
        ),
        exclude_presented=False,
        queue_context=queue_context,
        continuation_tokens={},
    )


def _interleaved_sources(
    targets: list[dict[str, str | None]],
    results: list[ToolResult],
    scope: dict[str, Any],
) -> list[tuple[dict, str]]:
    buckets: list[list[tuple[dict, str]]] = []
    for target, result in zip(targets, results, strict=False):
        if not result.ok:
            buckets.append([])
            continue
        label = str(target.get("label") or "")
        buckets.append(
            [
                (place, label)
                for place in search_local_places._provider_places(result)
                if search_local_places._target_accepts_place(place, target, scope)
            ]
        )
    source: list[tuple[dict, str]] = []
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        source.extend(bucket[index] for bucket in buckets if index < len(bucket))
    return source


async def _persist(
    *,
    source_places: list[tuple[dict, str]],
    query: str,
    scope: dict[str, Any],
    open_now: bool | None,
    max_results: int,
    ctx: ToolContext,
    session_id: str,
    timings: dict[str, float],
    unverified_names: list[str],
    operation: str,
    requested_count: int,
    coverage: dict[str, Any],
    exclude_presented: bool,
    queue_context: dict[str, Any],
    continuation_tokens: dict[str, str],
) -> ToolResult:
    presented = {
        str(entry.get("canonical_identity") or "")
        for entry in discovery_store.presented_entity_registry(ctx.session)
        if str(entry.get("canonical_identity") or "")
    }
    normalized_places: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for place, search_area in source_places:
        ranked = search_local_places._normalize_discovery_place(
            place, query, search_area
        )
        if ranked is None:
            continue
        if open_now is True and ranked["open_status"] == "closed":
            continue
        if not search_local_places._authorized_for_scope(ranked, scope):
            continue
        identity = search_local_places._place_identity(ranked)
        if exclude_presented and discovery_store._identity_key(ranked) in presented:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        distance = _rider_distance_meters(ranked, ctx)
        if distance is not None:
            ranked["rider_distance_meters"] = distance
        normalized_places.append(ranked)

    if scope["kind"] == "current_location":
        normalized_places = _project_current_location_relevance(
            normalized_places, ctx, max_results
        )

    raw_places: list[dict] = []
    for ranked in normalized_places:
        ranking = search_local_places.baseline_ranking(ranked)
        ranked["baseline_score"] = ranking["baseline_score"]
        ranked["ranking_factors"] = ranking["ranking_factors"]
        raw_places.append(ranked)
        if len(raw_places) >= max_results:
            break

    if not raw_places:
        return ToolResult(
            ok=True,
            outcome=ToolOutcome.UNAVAILABLE,
            data={
                "discovery_set_id": None,
                "query": query,
                "scope": scope,
                "operation": operation,
                "places": [],
                "unverified_names": unverified_names,
                "requested_count": requested_count,
                "coverage": coverage,
                "exhausted": bool(exclude_presented),
                "additional_options": False if exclude_presented else None,
            },
            summary="Place search completed",
            timings=timings,
        )

    set_id = discovery_store.store_discovery_set(
        session_id=session_id,
        session=ctx.session if isinstance(ctx.session, dict) else None,
        places=raw_places,
        query=query,
        search_scope=scope,
        requested_count=requested_count,
        coverage=coverage,
        queue_context=queue_context,
        continuation_tokens=continuation_tokens,
    )
    record = discovery_store.load_discovery_set(set_id, session_id=session_id) or {}
    stored_places = [
        place for place in record.get("places") or [] if isinstance(place, dict)
    ]
    model_places = []
    for place in stored_places:
        model_place = search_local_places._model_place(place)
        model_place.pop("latitude", None)
        model_place.pop("longitude", None)
        model_places.append(model_place)
    model_places, queue_max_wait = await _queue_digest(
        model_places,
        stored_places,
        queue_context,
        ctx,
    )
    if isinstance(ctx.session, dict):
        trip_state_module.bind_discovery_set(ctx.session, set_id)
    evidence = getattr(ctx, "turn_evidence", None)
    if evidence is not None:
        evidence.note_discover_places(
            ok=True,
            discovery_set_id=set_id,
            place_count=len(model_places),
            operation=operation,
        )
    data: dict[str, Any] = {
        "discovery_set_id": set_id,
        "query": query,
        "scope": scope,
        "operation": operation,
        "places": model_places,
        "unverified_names": unverified_names,
        "requested_count": requested_count,
        "coverage": coverage,
    }
    if queue_max_wait is not None:
        data["queue_max_wait_minutes"] = queue_max_wait
    return ToolResult(
        ok=True,
        data=data,
        summary="Place search completed",
        timings=timings,
    )


def _observation_time(value: object) -> datetime:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return parsed
    return datetime.now(UTC)


async def _queue_digest(
    model_places: list[dict[str, Any]],
    stored_places: list[dict[str, Any]],
    queue_context: dict[str, Any],
    ctx: ToolContext,
) -> tuple[list[dict[str, Any]], float | None]:
    mode = str(queue_context.get("mode") or "ignore")
    if mode in {"ignore", "heads_up"}:
        return model_places, None

    when = _observation_time(getattr(ctx, "now_et", None))
    stored_by_id = {
        str(place.get("place_id") or ""): place for place in stored_places
    }
    supported_ids = [
        place_id
        for place in stored_places
        if (place_id := str(place.get("provider_place_id") or "").strip())
        and damn_lines.get_supported_venue(place_id) is not None
    ]
    observations: dict[str, damn_lines.QueueObservation] = {}
    provider_available = False
    if mode != "historical" and supported_ids:
        try:
            current = await damn_lines.get_current_observations(
                supported_ids, now=when
            )
            observations = current.observations
            provider_available = current.provider_available
        except (RuntimeError, TypeError, ValueError) as exc:
            _LOGGER.warning(
                "Damn Lines current queue lookup failed type=%s",
                type(exc).__name__,
            )
            observations = {}
            provider_available = False

    enriched: list[dict[str, Any]] = []
    for model in model_places:
        stored = stored_by_id.get(str(model.get("place_id") or ""), {})
        item = dict(model)
        item["queue_evidence"] = _place_queue_evidence(
            stored,
            observations=observations,
            mode=mode,
            when=when,
            provider_available=provider_available,
        )
        enriched.append(item)

    max_wait = queue_context.get("max_wait_minutes")
    queue_max = max_wait if mode == "decision" and max_wait is not None else None
    return enriched, queue_max


def _place_queue_evidence(
    stored: dict[str, Any],
    *,
    observations: dict[str, damn_lines.QueueObservation],
    mode: str,
    when: datetime,
    provider_available: bool,
) -> dict[str, Any]:
    place_id = str(stored.get("provider_place_id") or "").strip()
    if damn_lines.get_supported_venue(place_id) is None:
        return {"coverage": "unmonitored"}
    if mode == "historical":
        pattern = _historical_pattern(place_id, when)
        return (
            _historical_evidence(pattern)
            if pattern is not None
            else {"coverage": "supported", "evidence_kind": "unavailable"}
        )
    observation = observations.get(place_id)
    if observation is not None:
        evidence: dict[str, Any] = {
            "coverage": "supported",
            "evidence_kind": "current",
            "captured_at": observation.captured_at.isoformat(),
        }
        if observation.people_count is not None:
            evidence["people_count"] = observation.people_count
        if observation.wait_minutes is not None:
            evidence["wait_minutes"] = observation.wait_minutes
        return evidence
    if stored.get("open_status") == "open":
        pattern = _historical_pattern(place_id, when)
        if pattern is not None:
            return {**_historical_evidence(pattern), "current_available": False}
    return {
        "coverage": "supported",
        "evidence_kind": "unavailable",
        "current_available": False,
        "provider_available": provider_available,
    }


def _historical_pattern(
    place_id: str, when: datetime
) -> damn_lines.HistoricalQueuePattern | None:
    try:
        return damn_lines.get_historical_pattern(place_id, when, now=when)
    except (TypeError, ValueError):
        return None


def _historical_evidence(
    pattern: damn_lines.HistoricalQueuePattern,
) -> dict[str, Any]:
    return {
        "coverage": "supported",
        "evidence_kind": "historical",
        "weekday": pattern.weekday,
        "hour": pattern.hour,
        "people_mean": pattern.people_mean,
        "wait_minutes_mean": pattern.wait_minutes_mean,
        "sample_count": pattern.sample_count,
        "comparable_dates": pattern.comparable_dates,
        "date_from": pattern.date_from.isoformat(),
        "date_to": pattern.date_to.isoformat(),
    }


def _target_key(index: int) -> str:
    return f"target_{index}"


def _matching_continuation_tokens(
    *,
    ctx: ToolContext,
    session_id: str,
    query: str,
    scope: dict[str, Any],
) -> dict[str, str]:
    if not isinstance(ctx.session, dict):
        return {}
    active_set_id = trip_state_module.get_trip_state(ctx.session).get(
        "active_discovery_set_id"
    )
    record = (
        discovery_store.load_discovery_set(active_set_id, session_id=session_id)
        if isinstance(active_set_id, str)
        else None
    )
    if not record:
        return {}
    same_query = " ".join(str(record.get("query") or "").casefold().split()) == (
        " ".join(query.casefold().split())
    )
    same_scope = record.get("search_scope") == discovery_store._sanitized_search_scope(
        scope
    )
    if not same_query or not same_scope:
        return {}
    tokens = record.get("continuation_tokens")
    return {
        key: token
        for key, token in (tokens.items() if isinstance(tokens, dict) else ())
        if isinstance(key, str) and isinstance(token, str) and token
    }


def _response_continuation_tokens(
    results: list[ToolResult],
) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for index, result in enumerate(results[:5]):
        data = result.data if isinstance(result.data, dict) else {}
        token = data.get("next_page_token")
        if result.ok and isinstance(token, str) and token:
            tokens[_target_key(index)] = token
    return tokens


async def _provider_results(
    requests: list[Awaitable[ToolResult]],
) -> list[ToolResult]:
    raw_results = await asyncio.gather(*requests, return_exceptions=True)
    results: list[ToolResult] = []
    for result in raw_results:
        if isinstance(result, Exception):
            _LOGGER.warning(
                "place discovery provider failed type=%s",
                type(result).__name__,
            )
            results.append(
                ToolResult(
                    ok=False,
                    error="place search is temporarily unavailable",
                )
            )
            continue
        if isinstance(result, BaseException):
            raise result
        results.append(result)
    return results


def _name_matches(requested: str, place: dict) -> bool:
    wanted = discovery_store._normalized_name(requested)
    actual = discovery_store._normalized_name(
        place.get("name") or place.get("display_name")
    )
    if not wanted or not actual:
        return False
    return (
        actual == wanted
        or actual.startswith(wanted + " ")
        or wanted.startswith(actual + " ")
    )


def _project_current_location_relevance(
    places: list[dict], ctx: ToolContext, max_results: int
) -> list[dict]:
    origin = _coordinates(ctx.origin)
    if origin is None or len(places) <= max_results:
        return places
    measured: list[tuple[float, int, dict]] = []
    unknown: list[tuple[int, dict]] = []
    for index, place in enumerate(places):
        point = _coordinates(
            {"lat": place.get("latitude"), "lng": place.get("longitude")}
        )
        if point is None:
            unknown.append((index, place))
            continue
        measured.append((geo.distance_meters(*origin, *point), index, place))
    if not measured:
        return places
    selected = {
        id(place) for _distance, _index, place in sorted(measured)[:max_results]
    }
    remaining = max_results - len(selected)
    selected.update(id(place) for _index, place in unknown[:remaining])
    # Filter the original provider sequence instead of concatenating measured
    # and unknown groups, so any unknown-coordinate candidates kept for spare
    # capacity retain their provider-relative order.
    return [place for place in places if id(place) in selected]


def _coordinates(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        latitude = float(value.get("lat", value.get("latitude")))
        longitude = float(value.get("lng", value.get("longitude")))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    return latitude, longitude


def _rider_distance_meters(place: dict, ctx: ToolContext) -> float | None:
    origin = _coordinates(ctx.origin)
    point = _coordinates({"lat": place.get("latitude"), "lng": place.get("longitude")})
    if origin is None or point is None:
        return None
    return round(geo.distance_meters(*origin, *point), 1)
