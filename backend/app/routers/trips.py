import base64
import asyncio
import importlib
import os
import re
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.services.ai_advisor import stream_recommendation
from app.services.mta_feed import fetch_service_alerts, get_stalled_buses, parse_service_alerts, filter_alerts_for_routes, get_stalled_trains
from app.services.voice import generate_speech
from app.services.trips import text, scoring, candidates, enrichment, incidents as trip_incidents

directions_service = importlib.import_module("app.services.directions")
get_transit_route = directions_service.get_transit_route
parse_response = directions_service.parse_response


class _UnavailableGoogleRoutesError(RuntimeError):
    pass


GoogleRoutesError = getattr(directions_service, "GoogleRoutesError", _UnavailableGoogleRoutesError)

router = APIRouter()

TRIP_CONTEXT_TIMEOUT_S = float(os.getenv("TRIP_CONTEXT_TIMEOUT_S", "2.0"))
# Haiku streams a full narration + per-candidate analysis block in ~3.4-3.6s
# (measured). A 4s ceiling left no margin, so any network jitter tripped the
# timeout -> the rider got the "could not complete live reasoning" fallback AND
# route 0 (selection is lost on timeout). 8s is ~2x the median: room to finish,
# still bounded.
TRIP_ADVISOR_TIMEOUT_S = float(os.getenv("TRIP_ADVISOR_TIMEOUT_S", "8.0"))
TRIP_TTS_TIMEOUT_S = float(os.getenv("TRIP_TTS_TIMEOUT_S", "4.0"))


async def _collect_recommendation(payload: dict) -> str:
    raw_recommendation = ""
    async for chunk in stream_recommendation(payload):
        raw_recommendation += chunk
    return raw_recommendation


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
        except GoogleRoutesError as exc:
            print(
                "[trip] routing provider failed "
                f"code={exc.code} provider_status={exc.provider_status or 'none'}"
            )
            if exc.code == "timeout":
                raise HTTPException(status_code=503, detail="Google Routes API timed out")
            if exc.code == "not_configured":
                raise HTTPException(status_code=500, detail="Routing provider is not configured")
            if exc.code.startswith("http_"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Upstream routing provider error ({exc.code})",
                )
            if exc.code == "request_failed":
                raise HTTPException(
                    status_code=502,
                    detail="Upstream routing provider network error",
                )
            if exc.code == "invalid_json":
                raise HTTPException(
                    status_code=502,
                    detail="Upstream routing provider returned invalid data",
                )
            raise HTTPException(
                status_code=502,
                detail=f"Upstream routing provider error ({exc.code})",
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
        incidents = list(trip_incidents._LAST_INCIDENTS)
        # Scan EVERY station on every candidate route (board, alight, and all
        # intermediate stops), not just the endpoints -- ATLAS watches the whole
        # journey for incidents. The list resolves from the static index, so this
        # stays off the DB and off the hot path.
        incidents_pending = trip_incidents._launch_incident_scan(trip_incidents._scan_station_names(gtfs, parsed_response))
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
        analysis_selected_index, candidate_analysis = candidates._parse_candidate_analysis(raw_recommendation)
        if not route_tag_match and analysis_selected_index is not None:
            chosen_index = analysis_selected_index
            if chosen_index >= len(parsed_response):
                chosen_index = 0
        recommendation = candidates._strip_model_control_blocks(raw_recommendation)
        recommendation = text._sanitize_recommendation(recommendation)

        # 8b. Enrich ONLY the chosen route's legs (subway + bus). Alternates are
        # returned un-enriched and filled in lazily on select.
        chosen_route = parsed_response[chosen_index] if parsed_response else []
        await enrichment._enrich_route(gtfs, chosen_route)
        marks["enrich"] = time.monotonic() - t0

        # 9. Generate speech (with tag stripped, abbreviations expanded for TTS)
        tts_text = text._expand_abbreviations(recommendation)
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
        route_candidates = candidates._build_route_candidates(
            parsed_response,
            chosen_index,
            candidate_analysis,
        )
        route_incidents = trip_incidents._build_route_incident_markers(incidents, chosen_route)
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
        metrics = await enrichment._enrich_route(gtfs, steps)
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
