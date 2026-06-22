import base64
import asyncio
import json
import os
import re
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.directions import get_transit_route, parse_response
from app.services.ai_advisor import stream_recommendation
from app.services.incident_monitor import get_incidents
from app.services.mta_feed import fetch_service_alerts, get_stalled_buses, parse_service_alerts, filter_alerts_for_routes, get_stalled_trains
from app.services.bus_routes import fetch_bus_route_stop_groups, slice_route_stops
from app.services.voice import generate_speech

router = APIRouter()

# The per-leg GTFS stop enrichment runs a GROUP BY over all of a route's
# stop_times on a remote Postgres; 1.25s was too tight and timed out, dropping
# the station NAMES (the map then showed unlabeled dots). Give the query real
# headroom -- it runs in a worker thread, so a longer wait never blocks the
# event loop. Tunable via env.
TRIP_GTFS_ENRICH_TIMEOUT_S = float(os.getenv("TRIP_GTFS_ENRICH_TIMEOUT_S", "6.0"))
TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
# Haiku streams a full narration + per-candidate analysis block in ~3.4-3.6s
# (measured). A 4s ceiling left no margin, so any network jitter tripped the
# timeout -> the rider got the "could not complete live reasoning" fallback AND
# route 0 (selection is lost on timeout). 8s is ~2x the median: room to finish,
# still bounded.
TRIP_ADVISOR_TIMEOUT_S = float(os.getenv("TRIP_ADVISOR_TIMEOUT_S", "8.0"))
TRIP_TTS_TIMEOUT_S = float(os.getenv("TRIP_TTS_TIMEOUT_S", "4.0"))
# Incident scan (Grok + X-search) is far slower than any trip budget, so it runs
# OFF the hot path: a single-flight background task with its own generous
# timeout, caching its last result for trips to read instantly.
TRIP_INCIDENT_SCAN_TIMEOUT_S = float(os.getenv("TRIP_INCIDENT_SCAN_TIMEOUT_S", "25.0"))

_LAST_INCIDENTS: list[dict] = []
_INCIDENT_SCAN_INFLIGHT = False
_INCIDENT_BG_TASKS: set = set()


async def _refresh_incidents_bg(stops: list[str]):
    """Best-effort background incident scan. Never blocks a trip response; caches
    its result in _LAST_INCIDENTS for subsequent trips to serve."""
    global _INCIDENT_SCAN_INFLIGHT, _LAST_INCIDENTS
    try:
        result = await asyncio.wait_for(
            get_incidents(stops), timeout=TRIP_INCIDENT_SCAN_TIMEOUT_S
        )
        incidents = result.get("incidents", []) if isinstance(result, dict) else []
        _LAST_INCIDENTS = incidents if isinstance(incidents, list) else []
        print(f"[trip] background incident scan: {len(_LAST_INCIDENTS)} incident(s)")
    except asyncio.TimeoutError:
        print(f"[trip] background incident scan timed out ({TRIP_INCIDENT_SCAN_TIMEOUT_S:.0f}s)")
    except Exception as exc:
        print(f"[trip] background incident scan failed: {exc!r}")
    finally:
        _INCIDENT_SCAN_INFLIGHT = False


def _launch_incident_scan(stops: list[str]) -> bool:
    """Fire-and-forget, single-flight incident refresh. Returns True when a scan
    is in progress, so the trip response can flag incidents as pending."""
    global _INCIDENT_SCAN_INFLIGHT
    if not stops:
        return False
    if _INCIDENT_SCAN_INFLIGHT:
        return True
    _INCIDENT_SCAN_INFLIGHT = True
    try:
        task = asyncio.create_task(_refresh_incidents_bg(stops))
    except Exception:
        _INCIDENT_SCAN_INFLIGHT = False
        return False
    # Keep a strong reference so the task is not garbage-collected mid-flight.
    _INCIDENT_BG_TASKS.add(task)
    task.add_done_callback(_INCIDENT_BG_TASKS.discard)
    return True

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

_CANDIDATE_ANALYSIS_PATTERN = re.compile(
    r"\[CANDIDATE_ANALYSIS\](.*?)\[/CANDIDATE_ANALYSIS\]",
    re.IGNORECASE | re.DOTALL,
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

def _parse_candidate_analysis(raw_text: str) -> tuple[int | None, dict[int, dict[str, str]]]:
    match = _CANDIDATE_ANALYSIS_PATTERN.search(raw_text or "")
    if not match:
        return None, {}

    try:
        payload = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None, {}

    selected_index = payload.get("selected_route_index")
    try:
        parsed_selected = int(selected_index) if selected_index is not None else None
    except (TypeError, ValueError):
        parsed_selected = None

    rows = payload.get("candidate_analysis")
    if not isinstance(rows, list):
        return parsed_selected, {}

    analysis: dict[int, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        is_recommended = bool(row.get("is_recommended"))
        generic_reason = row.get("reason") or ""
        recommendation_reason = _safe_text(
            row.get("recommendation_reason")
            or (generic_reason if is_recommended else "")
            or ""
        )
        rejection_reason = _safe_text(
            row.get("rejection_reason")
            or (generic_reason if not is_recommended else "")
            or ""
        )
        if recommendation_reason or rejection_reason:
            analysis[index] = {
                "recommendation_reason": recommendation_reason,
                "rejection_reason": rejection_reason,
            }

    return parsed_selected, analysis

def _strip_model_control_blocks(raw_text: str) -> str:
    without_route = re.sub(r"\s*\[ROUTE:\d+\]\s*", "", raw_text or "")
    return _CANDIDATE_ANALYSIS_PATTERN.sub("", without_route).strip()


async def _enrich_subway_step_with_gtfs(gtfs, step: dict) -> list[dict]:
    return await asyncio.wait_for(
        asyncio.to_thread(
            gtfs.get_intermediate_stops_with_coords,
            step["route_id"],
            step["departure_stop"],
            step["arrival_stop"],
            step.get("departure_coords"),
            step.get("arrival_coords"),
        ),
        timeout=TRIP_GTFS_ENRICH_TIMEOUT_S,
    )


async def _enrich_subway_legs(gtfs, steps: list[dict]) -> dict:
    """Enrich the SUBWAY steps of a single route in place (intermediate stop
    names + coordinates). Parallel, cached, strictly fail-open per leg. Returns
    {"legs": n, "with_stops": k}."""
    subway_steps = [s for s in steps if s.get("type") == "SUBWAY"]
    metrics = {"legs": len(subway_steps), "with_stops": 0}
    for step in subway_steps:
        step.setdefault("intermediate_stops", [])
        step.setdefault("intermediate_stop_locations", [])
    if not subway_steps or not gtfs:
        return metrics
    results = await asyncio.gather(
        *(_enrich_subway_step_with_gtfs(gtfs, step) for step in subway_steps),
        return_exceptions=True,
    )
    for step, result in zip(subway_steps, results):
        if isinstance(result, asyncio.TimeoutError):
            print(
                f"[trip] subway stop enrichment timed out "
                f"({step.get('route_id')}, {TRIP_GTFS_ENRICH_TIMEOUT_S:.2f}s)"
            )
            located = []
        elif isinstance(result, BaseException):
            print(f"[trip] subway stop enrichment skipped ({step.get('route_id')}): {result}")
            located = []
        else:
            located = result
        step["intermediate_stop_locations"] = located
        step["intermediate_stops"] = [s["name"] for s in located]
        if located:
            metrics["with_stops"] += 1
        else:
            print(
                "[trip] subway leg has no intermediate stops "
                f"({step.get('route_id')}: {step.get('departure_stop')} "
                f"-> {step.get('arrival_stop')})"
            )
    return metrics


async def _enrich_bus_legs(steps: list[dict]) -> dict:
    """Enrich the BUS steps of a single route in place via OneBusAway
    stops-for-route. Strictly fail-open. Returns {"legs": n, "with_stops": k}."""
    bus_steps = [s for s in steps if s.get("type") == "BUS"]
    metrics = {"legs": len(bus_steps), "with_stops": 0}
    for step in bus_steps:
        step.setdefault("intermediate_stops", [])
        step.setdefault("intermediate_stop_locations", [])
    bus_route_ids = sorted({s["route_id"] for s in bus_steps if s.get("route_id")})
    if not bus_route_ids:
        return metrics
    try:
        results = await asyncio.gather(
            *(fetch_bus_route_stop_groups(rid) for rid in bus_route_ids),
            return_exceptions=True,
        )
        groups_by_route = {
            rid: result
            for rid, result in zip(bus_route_ids, results)
            if isinstance(result, dict)
        }
        for step in bus_steps:
            parsed_groups = groups_by_route.get(step["route_id"])
            if not parsed_groups:
                continue
            located = slice_route_stops(
                parsed_groups,
                step.get("departure_coords") or {},
                step.get("arrival_coords") or {},
            )
            if located:
                step["intermediate_stop_locations"] = located
                step["intermediate_stops"] = [s["name"] for s in located]
                metrics["with_stops"] += 1
    except Exception as exc:
        print(f"[trip] bus stop enrichment skipped: {exc}")
    return metrics


async def _enrich_route(gtfs, route: list[dict]) -> dict:
    """Enrich one route's SUBWAY + BUS legs in place. Returns leg metrics."""
    sub = await _enrich_subway_legs(gtfs, route)
    bus = await _enrich_bus_legs(route)
    return {
        "subway_legs": sub["legs"],
        "subway_with_stops": sub["with_stops"],
        "bus_legs": bus["legs"],
        "bus_with_stops": bus["with_stops"],
    }


async def _collect_recommendation(payload: dict) -> str:
    raw_recommendation = ""
    async for chunk in stream_recommendation(payload):
        raw_recommendation += chunk
    return raw_recommendation


def _step_minutes(step: dict) -> int:
    if step.get("type") in ("SUBWAY", "BUS"):
        minutes = step.get("minutes_until_arrival")
        if isinstance(minutes, (int, float)):
            return max(1, round(minutes))
        return 8
    return 4

def _route_total_minutes(route: list[dict]) -> int:
    for step in route or []:
        route_total = step.get("route_total_minutes")
        if isinstance(route_total, (int, float)):
            return max(1, round(route_total))
    live_arrivals = [
        step.get("minutes_until_arrival")
        for step in route or []
        if step.get("type") in ("SUBWAY", "BUS")
        and isinstance(step.get("minutes_until_arrival"), (int, float))
    ]
    if live_arrivals:
        return max(1, round(max(live_arrivals)))
    return max(1, sum(_step_minutes(step) for step in route))

def _route_transfer_count(route: list[dict]) -> int:
    transit_steps = [step for step in route if step.get("type") in ("SUBWAY", "BUS")]
    return max(0, len(transit_steps) - 1)

def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route or []:
        if step.get("type") not in ("SUBWAY", "BUS"):
            continue
        line = _step_route_id(step)
        if line and line not in lines:
            lines.append(line)
    return lines

def _route_alert_hits(route: list[dict], alerts: list[dict] | None) -> list[str]:
    route_lines = set(_route_lines(route))
    hits: list[str] = []
    for alert in alerts or []:
        alert_routes = {
            str(route_id or "").strip().upper()
            for route_id in alert.get("route_ids", [])
            if str(route_id or "").strip()
        }
        if route_lines & alert_routes:
            title = _safe_text(alert.get("header") or "active alert", 80)
            if title and title not in hits:
                hits.append(title)
    return hits

def _route_score(route: list[dict], alerts: list[dict] | None) -> dict:
    total_minutes = _route_total_minutes(route)
    transfers = _route_transfer_count(route)
    alert_hits = _route_alert_hits(route, alerts)
    transit_count = len(_route_lines(route))
    score = total_minutes + transfers * 4 + len(alert_hits) * 8
    return {
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "transit_count": transit_count,
        "score": score,
        "alerts": alert_hits[:2],
    }

def _score_routes(routes: list[list[dict]], alerts: list[dict] | None) -> list[dict]:
    scored = []
    for index, route in enumerate(routes):
        score = _route_score(route, alerts)
        scored.append({"index": index, **score})
    scored.sort(
        key=lambda row: (
            row["score"],
            row["total_minutes"],
            row["transfers"],
            row["index"],
        )
    )
    rank_by_index = {row["index"]: rank + 1 for rank, row in enumerate(scored)}
    for row in scored:
        row["rank"] = rank_by_index[row["index"]]
    return scored

def _score_by_index(scored_routes: list[dict]) -> dict[int, dict]:
    return {int(row["index"]): row for row in scored_routes}

def _build_fallback_candidate_reason(
    route: list[dict],
    chosen_route: list[dict],
    is_recommended: bool,
    route_score: dict | None = None,
    chosen_score: dict | None = None,
) -> str:
    route_score = route_score or _route_score(route, [])
    chosen_score = chosen_score or _route_score(chosen_route, [])
    if is_recommended:
        alert_phrase = (
            " with no active alert penalty"
            if route_score.get("alert_count", 0) == 0
            else f" despite {route_score['alert_count']} alert(s) on its lines"
        )
        return (
            f"Best overall score: {route_score['total_minutes']} min, "
            f"{route_score['transfers']} transfer(s){alert_phrase}."
        )

    route_minutes = int(route_score["total_minutes"])
    chosen_minutes = int(chosen_score["total_minutes"])
    delay = route_minutes - chosen_minutes
    transfer_delta = int(route_score["transfers"]) - int(chosen_score["transfers"])
    alert_delta = int(route_score["alert_count"]) - int(chosen_score["alert_count"])
    if delay >= 3:
        return f"Slower by about {delay} minutes under current service conditions."
    if transfer_delta > 0:
        return "Adds an extra transfer, which weakens reliability right now."
    if alert_delta > 0:
        return "Touches more active service alerts than the selected route."
    return "Available, but less reliable than the recommended route right now."

def _build_route_candidates(
    routes: list[list[dict]],
    chosen_index: int,
    candidate_analysis: dict[int, dict[str, str]],
    scored_routes: list[dict] | None = None,
) -> list[dict]:
    chosen_route = routes[chosen_index] if routes else []
    scores = _score_by_index(scored_routes or _score_routes(routes, []))
    chosen_score = scores.get(chosen_index, _route_score(chosen_route, []))
    candidates = []
    for index, route in enumerate(routes):
        is_recommended = index == chosen_index
        analysis = candidate_analysis.get(index, {})
        route_score = scores.get(index, _route_score(route, []))
        fallback = _build_fallback_candidate_reason(
            route,
            chosen_route,
            is_recommended,
            route_score,
            chosen_score,
        )
        candidates.append(
            {
                "id": f"candidate-{index}",
                "index": index,
                "steps": route,
                "is_recommended": is_recommended,
                "total_minutes": route_score["total_minutes"],
                "selection_score": route_score["score"],
                "selection_rank": route_score.get("rank", index + 1),
                "score_breakdown": {
                    "duration_minutes": route_score["total_minutes"],
                    "transfers": route_score["transfers"],
                    "active_alerts": route_score["alert_count"],
                    "transit_lines": _route_lines(route),
                },
                # Only the chosen route is enriched on the initial response;
                # alternates carry empty intermediate-stop lists and are filled
                # in lazily via POST /api/trip/enrich-route when selected.
                "enriched": is_recommended,
                "can_enrich_on_select": not is_recommended,
                "recommendation_reason": (
                    analysis.get("recommendation_reason") or fallback
                    if is_recommended
                    else None
                ),
                "rejection_reason": (
                    analysis.get("rejection_reason") or fallback
                    if not is_recommended
                    else None
                ),
            }
        )
    return candidates

def _station_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

def _step_route_id(step: dict) -> str:
    return str(step.get("route_id") or step.get("train_line") or "").strip().upper()

def _coords_from_stop(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat = value.get("lat", value.get("latitude"))
    lng = value.get("lng", value.get("longitude"))
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None
    return float(lat), float(lng)

def _route_stop_index(route: list[dict]) -> dict[str, dict]:
    stops: dict[str, dict] = {}

    def add_stop(name: object, coords: tuple[float, float] | None, route_id: str) -> None:
        key = _station_key(name)
        if not key:
            return
        row = stops.setdefault(
            key,
            {
                "name": _safe_text(name, 80),
                "lat": None,
                "lng": None,
                "route_ids": set(),
            },
        )
        if coords and row["lat"] is None and row["lng"] is None:
            row["lat"], row["lng"] = coords
        if route_id:
            row["route_ids"].add(route_id)

    for step in route or []:
        route_id = _step_route_id(step)
        if step.get("type") not in ("SUBWAY", "BUS"):
            continue
        add_stop(step.get("departure_stop"), _coords_from_stop(step.get("departure_coords")), route_id)
        add_stop(step.get("arrival_stop"), _coords_from_stop(step.get("arrival_coords")), route_id)
        for stop in step.get("intermediate_stop_locations") or []:
            if not isinstance(stop, dict):
                continue
            add_stop(stop.get("name"), _coords_from_stop(stop), route_id)

    return stops

def _build_route_incident_markers(incidents: list[dict], chosen_route: list[dict]) -> list[dict]:
    stop_index = _route_stop_index(chosen_route)
    markers: list[dict] = []
    allowed_severities = {"low", "medium", "high", "critical"}

    for incident in incidents or []:
        if not isinstance(incident, dict):
            continue
        station_name = (
            incident.get("nearby_station")
            or incident.get("station")
            or incident.get("stop_name")
        )
        stop = stop_index.get(_station_key(station_name))
        if not stop or stop.get("lat") is None or stop.get("lng") is None:
            continue

        severity = str(incident.get("severity") or "medium").strip().lower()
        if severity not in allowed_severities:
            severity = "medium"
        detail_parts = [
            _safe_text(incident.get("location"), 80),
            _safe_text(incident.get("source"), 40),
        ]
        detail = " · ".join(part for part in detail_parts if part)
        description = _safe_text(incident.get("description"), 180)

        markers.append(
            {
                "id": f"route-incident-{len(markers)}",
                "type": "incident",
                "lat": stop["lat"],
                "lng": stop["lng"],
                "title": description or "Incident reported near this route.",
                "detail": detail,
                "severity": severity,
                "source": _safe_text(incident.get("source"), 40),
                "station": stop["name"],
                "routeIds": sorted(stop["route_ids"]),
                "active": severity in ("high", "critical"),
            }
        )

    return markers

def _scan_station_names(gtfs, routes) -> list[str]:
    """Every station the rider could encounter across all candidate routes --
    board, alight, AND intermediate stops -- deduped by normalized name. This is
    the full set ATLAS scans for incidents, so it watches the whole journey, not
    just the endpoints. Intermediate stops resolve straight from the in-memory
    static pattern index (always instant); if that index is absent we fall back
    to endpoints only rather than ever touching the remote DB on this path."""
    seen: set[str] = set()
    names: list[str] = []

    def add(value):
        key = _station_key(value)
        if key and key not in seen:
            seen.add(key)
            names.append(_safe_text(value, 80))

    index = getattr(gtfs, "_pattern_index", None) if gtfs else None
    for route in routes or []:
        for step in route:
            if step.get("type") not in ("SUBWAY", "BUS"):
                continue
            add(step.get("departure_stop"))
            add(step.get("arrival_stop"))
            if step.get("type") == "SUBWAY" and index and step.get("route_id"):
                try:
                    rows, _meta = index.get_intermediate_stops_with_coords(
                        step["route_id"],
                        step.get("departure_stop"),
                        step.get("arrival_stop"),
                        step.get("departure_coords"),
                        step.get("arrival_coords"),
                    )
                except Exception:
                    rows = []
                for row in rows or []:
                    add(row.get("name"))
    return names


class TripRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination: str
    destination_lat: float | None = None
    destination_lng: float | None = None

@router.post("/api/trip")
async def plan_trip(request: Request, payload: TripRequest):
    t0 = time.monotonic()
    marks: dict[str, float] = {}  # stage -> cumulative seconds, for the timing log
    try:
        gtfs = getattr(request.app.state, "gtfs", None)

        # 1. Get routes from Google
        dest_coords = (
            (payload.destination_lat, payload.destination_lng)
            if payload.destination_lat is not None and payload.destination_lng is not None
            else None
        )
        try:
            response = await get_transit_route(
                (payload.origin_lat, payload.origin_lng),
                payload.destination,
                dest_coords,
            )
        except RuntimeError as exc:
            msg = str(exc)
            if "timed out" in msg.lower():
                raise HTTPException(status_code=503, detail="Google Routes API timed out")
            if "Google Routes API" in msg:
                raise HTTPException(status_code=502, detail="Upstream routing provider error")
            raise
        marks["directions"] = time.monotonic() - t0

        # 2. Parse the response into structured routes
        parsed_response = parse_response(response)

        # 3. Collect route ids, station names, and bus route ids from ALL
        # candidate routes (cheap, no DB). Intermediate-stop enrichment is
        # DEFERRED: only the CHOSEN route is enriched (after the advisor picks
        # it, below). Enriching every Google candidate's legs against the remote
        # GTFS DB was the dominant request latency; alternates are now enriched
        # lazily when the rider selects them (POST /api/trip/enrich-route).
        route_ids = set()
        bus_route_ids = set()
        for route in parsed_response:
            for step in route:
                step_type = step["type"]
                if step_type in ("SUBWAY", "BUS"):
                    route_ids.add(step["route_id"])
                    # Stable response shape: every transit step always carries
                    # these keys, empty until (and unless) its route is enriched.
                    step["intermediate_stops"] = []
                    step["intermediate_stop_locations"] = []
                if step_type == "BUS":
                    bus_route_ids.add(step["route_id"])

        # 4. Fetch the FAST live context (alerts + stalled vehicles) in parallel.
        # Incidents are deliberately EXCLUDED here: the Grok + X-search scan is
        # far slower than any trip budget and used to poison this gather -- one
        # slow call hitting the timeout discarded the alerts/stalled results too,
        # costing a guaranteed ~2s every trip for nothing. Incidents now run off
        # the hot path (block 4b). return_exceptions keeps one slow upstream from
        # 500-ing the whole trip.
        try:
            raw_alerts, stalled, stalled_buses = await asyncio.wait_for(
                asyncio.gather(
                    fetch_service_alerts(),
                    get_stalled_trains(route_ids),
                    get_stalled_buses(bus_route_ids),
                    return_exceptions=True,
                ),
                timeout=TRIP_CONTEXT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            print(f"[trip] live context timed out ({TRIP_CONTEXT_TIMEOUT_S:.2f}s); continuing without")
            raw_alerts, stalled, stalled_buses = [], [], []
        for name, value in (
            ("alerts", raw_alerts),
            ("stalled_trains", stalled),
            ("stalled_buses", stalled_buses),
        ):
            if isinstance(value, BaseException):
                print(f"[trip] {name} fetch failed ({type(value).__name__}); continuing without")
        if isinstance(raw_alerts, BaseException):
            raw_alerts = []
        if isinstance(stalled, BaseException):
            stalled = []
        if isinstance(stalled_buses, BaseException):
            stalled_buses = []
        marks["gather"] = time.monotonic() - t0

        # 4b. Incidents are best-effort and OFF the hot path. Serve the most
        # recent background scan (possibly empty) and kick off a single-flight
        # refresh for next time -- the trip never awaits Grok.
        incidents = list(_LAST_INCIDENTS)
        # Scan EVERY station on every candidate route (board, alight, and all
        # intermediate stops), not just the endpoints -- ATLAS watches the whole
        # journey for incidents. The list resolves from the static index, so this
        # stays off the DB and off the hot path.
        incidents_pending = _launch_incident_scan(_scan_station_names(gtfs, parsed_response))
        marks["incidents"] = time.monotonic() - t0

        # 5. Filter alerts for relevant routes
        parsed_alerts = parse_service_alerts(raw_alerts) if raw_alerts else []
        relevant_alerts = filter_alerts_for_routes(parsed_alerts, route_ids)

        # 6. Build payload for ATLAS (routes + alerts + incidents + stalled trains + stalled buses).
        # ATLAS makes the route decision itself from these raw signals -- there is
        # deliberately no precomputed "best route" score in the payload, so the
        # model reasons over the full data rather than deferring to a number.
        jarvis_payload = {
            "routes": parsed_response,
            "service_alerts": relevant_alerts,
            "incidents": incidents if incidents else [],
            "stalled_trains": stalled if stalled else [],
            "stalled_buses": stalled_buses if stalled_buses else [],
        }

        # 7. Stream to Claude. The advisor is non-essential to DISPLAYING a
        # route: if it times out or errors (no credits, overload, network), an
        # unhandled exception here would 500 the whole trip. Fall back to a
        # plain non-mock rider-facing line so the real route still ships.
        raw_recommendation = ""
        try:
            raw_recommendation = await asyncio.wait_for(
                _collect_recommendation(jarvis_payload),
                timeout=TRIP_ADVISOR_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            print(
                f"[trip] advisor timed out ({TRIP_ADVISOR_TIMEOUT_S:.2f}s), "
                "using text-only fallback"
            )
            raw_recommendation = (
                "[ROUTE:0] ATLAS could not complete live reasoning, but the route shown "
                "is still built from real-time transit data."
            )
        except Exception as exc:
            print(f"[trip] advisor unavailable, using text-only fallback: {exc!r}")
            raw_recommendation = (
                "[ROUTE:0] ATLAS could not complete live reasoning, but the route shown "
                "is still built from real-time transit data."
            )
        marks["advisor"] = time.monotonic() - t0

        # 8. Parse chosen route index from ATLAS tag, then strip it
        chosen_index = 0
        route_tag_match = re.search(r"\[ROUTE:(\d+)\]", raw_recommendation)
        if route_tag_match:
            chosen_index = int(route_tag_match.group(1))
            if chosen_index >= len(parsed_response):
                chosen_index = 0
        analysis_selected_index, candidate_analysis = _parse_candidate_analysis(raw_recommendation)
        if not route_tag_match and analysis_selected_index is not None:
            chosen_index = analysis_selected_index
            if chosen_index >= len(parsed_response):
                chosen_index = 0
        recommendation = _strip_model_control_blocks(raw_recommendation)
        recommendation = _sanitize_recommendation(recommendation)

        # 8b. Enrich ONLY the chosen route's legs (subway + bus). Alternates are
        # returned un-enriched and filled in lazily on select.
        chosen_route = parsed_response[chosen_index] if parsed_response else []
        await _enrich_route(gtfs, chosen_route)
        marks["enrich"] = time.monotonic() - t0

        # 9. Generate speech (with tag stripped, abbreviations expanded for TTS)
        tts_text = _expand_abbreviations(recommendation)
        try:
            audio_bytes = await asyncio.wait_for(
                asyncio.to_thread(generate_speech, tts_text),
                timeout=TRIP_TTS_TIMEOUT_S,
            )
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        except asyncio.TimeoutError:
            print(f"[trip] TTS timed out ({TRIP_TTS_TIMEOUT_S:.2f}s), returning text-only response")
            audio_b64 = ""
        except Exception as exc:
            print(f"[trip] TTS unavailable, returning text-only response: {exc}")
            audio_b64 = ""
        marks["tts"] = time.monotonic() - t0

        # 10. Return response — only the chosen route goes to the frontend.
        # Candidate REASON text (why each alternate wasn't picked) still falls
        # back to a time/transfer/alert comparison when ATLAS doesn't supply its
        # own reason; that is display copy only and never changes the selection.
        route_candidates = _build_route_candidates(
            parsed_response,
            chosen_index,
            candidate_analysis,
        )
        route_incidents = _build_route_incident_markers(incidents, chosen_route)
        elapsed = time.monotonic() - t0
        # Single per-trip log line: time taken for each pipeline step + total.
        _d = lambda cur, prev: max(0.0, marks.get(cur, 0.0) - marks.get(prev, 0.0))
        print(
            f"[trip] directions={marks.get('directions', 0.0):.2f}s "
            f"gather={_d('gather', 'directions'):.2f}s "
            f"incidents={_d('incidents', 'gather'):.2f}s "
            f"advisor={_d('advisor', 'incidents'):.2f}s "
            f"enrich={_d('enrich', 'advisor'):.2f}s "
            f"tts={_d('tts', 'enrich'):.2f}s "
            f"total={elapsed:.2f}s"
        )
        return {
            "recommendation": recommendation,
            "audio": audio_b64,
            "route": chosen_route,
            "selected_route_index": chosen_index,
            "route_candidates": route_candidates,
            "alerts": relevant_alerts,
            "incidents": route_incidents,
            # Incidents are scanned off the hot path (background, best-effort).
            # True => a scan is in flight and markers may appear on a later trip.
            "incidents_pending": incidents_pending,
        }

    except HTTPException:
        raise
    except Exception:
        import traceback
        # Full detail goes to the server log only; the public 500 stays generic
        # so internal exception text is never exposed to the browser.
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Trip planning failed")


class EnrichRouteRequest(BaseModel):
    steps: list[dict]


@router.post("/api/trip/enrich-route")
async def enrich_route(request: Request, payload: EnrichRouteRequest):
    """Lazily enrich an alternate route's legs (intermediate stop names +
    coordinates) when the rider selects it. The initial /api/trip response only
    enriches the chosen route to keep latency low; alternates come back with
    can_enrich_on_select=true and call this on demand. Fail-open: the enriched
    steps are returned (possibly with empty stop lists) and never 500."""
    t0 = time.monotonic()
    gtfs = getattr(request.app.state, "gtfs", None)
    steps = payload.steps or []
    _q0 = getattr(gtfs, "_query_count", 0) if gtfs else 0
    try:
        metrics = await _enrich_route(gtfs, steps)
    except Exception as exc:
        print(f"[enrich-route] failed, returning un-enriched: {exc!r}")
        return {"steps": steps, "enriched": False}
    print(
        f"[enrich-route] subway_legs={metrics['subway_legs']} bus_legs={metrics['bus_legs']} "
        f"legs_with_stops={metrics['subway_with_stops'] + metrics['bus_with_stops']} "
        f"db_queries={(getattr(gtfs, '_query_count', 0) - _q0) if gtfs else 0} "
        f"total={time.monotonic()-t0:.2f}s"
    )
    return {"steps": steps, "enriched": True}
