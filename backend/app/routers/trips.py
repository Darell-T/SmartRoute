import asyncio
import importlib
import os
import math
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from app.services.ai_advisor import stream_recommendation
from app.services.mta_feed import fetch_service_alerts, get_stalled_buses, parse_service_alerts, filter_alerts_for_routes, get_stalled_trains
from app.services.trips import text, scoring, candidates, enrichment, incidents as trip_incidents, advisor_context
from app.services.trips.itinerary import build_canonical_itinerary
from app.services.trips.recommendation_reasons import (
    build_recommendation_reasons,
    format_recommendation_reason,
)
from app.services.validation import production_shadow
from app.services.validation.shadow import ShadowEvaluationStatus
from app.services import admission
from app.routers.trip_enrichment import (
    EnrichRouteRequest,
    _enrichment_steps_are_bounded,
    enrich_route,
)

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


async def _collect_recommendation(payload: dict) -> str:
    raw_recommendation = ""
    async for chunk in stream_recommendation(payload):
        raw_recommendation += chunk
    return raw_recommendation


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_lat: float
    origin_lng: float
    destination: str
    destination_lat: float | None = None
    destination_lng: float | None = None



def _trip_payload_is_bounded(payload: TripRequest) -> bool:
    coordinates = (payload.origin_lat, payload.origin_lng, payload.destination_lat, payload.destination_lng)
    if any(value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)) for value in coordinates):
        return False
    if not (40.2 <= payload.origin_lat <= 41.2 and -74.6 <= payload.origin_lng <= -73.2):
        return False
    if (payload.destination_lat is None) != (payload.destination_lng is None):
        return False
    if payload.destination_lat is not None and not (40.2 <= payload.destination_lat <= 41.2 and -74.6 <= payload.destination_lng <= -73.2):
        return False
    return isinstance(payload.destination, str) and bool(payload.destination.strip()) and len(payload.destination) <= 300

@router.post("/api/trip")
async def plan_trip(request: Request, payload: TripRequest):
    t0 = time.monotonic()
    marks: dict[str, float] = {}  # stage -> cumulative seconds, for the timing log
    lease = None
    incident_task: asyncio.Task[dict] | None = None
    try:
        if not _trip_payload_is_bounded(payload):
            raise HTTPException(status_code=400, detail="Invalid trip request")
        try:
            lease = await admission.acquire(
                admission.principal_from_request(request.headers.get("X-SmartRoute-Principal")),
                "trip",
            )
        except admission.AdmissionDenied as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=("Request identity is invalid." if exc.status_code == 403 else "Request admission is temporarily unavailable." if exc.status_code == 503 else "Too many requests."),
                headers={"Retry-After": str(exc.retry_after_s)},
            ) from None
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
        route_ids, bus_route_ids = candidates._collect_route_and_bus_ids(parsed_response)

        # Start the bounded, cacheable incident scan before collecting MTA
        # context. Both are independent inputs to the advisor, so serializing
        # them only increases route-card latency.
        incident_context = trip_incidents.build_candidate_stop_context(
            gtfs,
            parsed_response,
        )
        incident_task = asyncio.create_task(
            trip_incidents.scan_route_incidents(incident_context),
            name="trip-incident-scan",
        )

        # 4. Fetch the fast live context (alerts + stalled vehicles) in parallel.
        # return_exceptions keeps one slow upstream from 500-ing the whole trip.
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

        # 4b. The incident task covers every candidate's intermediate stops.
        # It remains advisory-only and fails closed to no advisor evidence.
        incident_scan = await incident_task
        incidents = incident_scan["incidents"]
        marks["incidents"] = time.monotonic() - t0

        # 5. Filter alerts for relevant routes
        parsed_alerts = parse_service_alerts(raw_alerts) if raw_alerts else []
        relevant_alerts = filter_alerts_for_routes(parsed_alerts, route_ids)

        # 6. Build the SmartRoute advisor payload (routes + alerts + incidents + stalled vehicles).
        # The route advisor makes the decision itself from these raw signals -- there is
        # deliberately no precomputed "best route" score in the payload, so the
        # model reasons over the full data rather than deferring to a number.
        route_advisor_payload = advisor_context.build_advisor_payload(
            routes=parsed_response,
            service_alerts=relevant_alerts,
            incidents=incidents,
            stalled_trains=stalled,
            stalled_buses=stalled_buses,
            mode=advisor_context.PlanningMode.INTELLIGENCE,
        )

        # 7. Stream to Claude. The advisor is non-essential to DISPLAYING a
        # route: if it times out or errors (no credits, overload, network), an
        # unhandled exception here would 500 the whole trip. Fall back to a
        # plain non-mock rider-facing line so the real route still ships.
        raw_recommendation = ""
        advisor_status = ShadowEvaluationStatus.COMPLETE
        try:
            raw_recommendation = await asyncio.wait_for(
                _collect_recommendation(route_advisor_payload),
                timeout=TRIP_ADVISOR_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            advisor_status = ShadowEvaluationStatus.FALLBACK
            print(
                f"[trip] advisor timed out ({TRIP_ADVISOR_TIMEOUT_S:.2f}s), "
                "using text-only fallback"
            )
            raw_recommendation = (
                "[ROUTE:0] SmartRoute could not complete live reasoning, but the route shown "
                "is still built from real-time transit data."
            )
        except Exception as exc:
            advisor_status = ShadowEvaluationStatus.FALLBACK
            print(f"[trip] advisor unavailable, using text-only fallback: {exc!r}")
            raw_recommendation = (
                "[ROUTE:0] SmartRoute could not complete live reasoning, but the route shown "
                "is still built from real-time transit data."
            )
        marks["advisor"] = time.monotonic() - t0

        # 8. Parse the chosen route index from the advisor tag, then strip it.
        chosen_index, candidate_analysis = advisor_context.parse_advisor_selection(
            raw_recommendation,
            len(parsed_response),
        )
        recommendation = candidates._strip_model_control_blocks(raw_recommendation)
        recommendation = text._sanitize_recommendation(recommendation)

        # 8b. Enrich ONLY the chosen route's legs (subway + bus). Alternates are
        # returned un-enriched and filled in lazily on select.
        chosen_route = parsed_response[chosen_index] if parsed_response else []
        await enrichment._enrich_route(gtfs, chosen_route)
        marks["enrich"] = time.monotonic() - t0

        # 10. Return response - only the chosen route goes to the frontend.
        # Candidate REASON text (why each alternate wasn't picked) still falls
        # back to a time/transfer/alert comparison when ATLAS doesn't supply its
        # own reason; that is display copy only and never changes the selection.
        scored_routes = scoring._score_routes(parsed_response, relevant_alerts)
        route_candidates = candidates._build_route_candidates(
            parsed_response,
            chosen_index,
            candidate_analysis,
            scored_routes,
        )
        score_by_index = scoring._score_by_index(scored_routes)
        # The direct map planner uses the same canonical timing contract as
        # agent cards. Keep its established candidate fields as compatibility
        # aliases, but do not make the frontend derive a parallel trip total.
        origin_point = {
            "label": "Your location",
            "lat": payload.origin_lat,
            "lng": payload.origin_lng,
        }
        destination_point = {
            "label": payload.destination,
            "lat": payload.destination_lat,
            "lng": payload.destination_lng,
        }
        for index, candidate in enumerate(route_candidates):
            route = candidate.get("steps") or []
            reason = (
                candidate.get("recommendation_reason")
                if candidate.get("is_recommended")
                else candidate.get("rejection_reason")
            )
            structured_reasons = (
                build_recommendation_reasons(
                    score_by_index[chosen_index],
                    [
                        score
                        for score_index, score in score_by_index.items()
                        if score_index != chosen_index
                    ],
                )
                if index == chosen_index
                else []
            )
            if candidate.get("is_recommended"):
                rendered_reasons = [
                    rendered
                    for rendered in (
                        format_recommendation_reason(structured)
                        for structured in structured_reasons
                    )
                    if rendered
                ]
                if rendered_reasons:
                    candidate["recommendation_reason"] = rendered_reasons[0]
            itinerary = build_canonical_itinerary(
                route,
                origin=origin_point,
                destination=destination_point,
                reasons=structured_reasons,
                itinerary_id=str(candidate.get("id") or "") or None,
            )
            candidate["itinerary"] = itinerary
            candidate["structured_recommendation_reasons"] = structured_reasons
            candidate["total_minutes"] = max(
                0, round(int(itinerary["total_duration_seconds"]) / 60)
            )
            candidate.setdefault("score_breakdown", {})["transfers"] = int(
                itinerary["transfer_count"]
            )
            if itinerary.get("arrival_at"):
                candidate["arrival_at"] = itinerary["arrival_at"]
        elapsed = time.monotonic() - t0
        # Single per-trip log line: time taken for each pipeline step + total.
        _d = lambda cur, prev: max(0.0, marks.get(cur, 0.0) - marks.get(prev, 0.0))
        print(
            f"[trip] directions={marks.get('directions', 0.0):.2f}s "
            f"gather={_d('gather', 'directions'):.2f}s "
            f"incidents={_d('incidents', 'gather'):.2f}s "
            f"advisor={_d('advisor', 'incidents'):.2f}s "
            f"enrich={_d('enrich', 'advisor'):.2f}s "
            f"total={elapsed:.2f}s"
        )
        displayed_result = {
            "recommendation": recommendation,
            "route": chosen_route,
            "selected_route_index": chosen_index,
            "route_candidates": route_candidates,
            "alerts": relevant_alerts,
        }
        baseline_payload = advisor_context.build_advisor_payload(
            routes=parsed_response,
            service_alerts=relevant_alerts,
            mode=advisor_context.PlanningMode.BASELINE,
        )

        async def _evaluate_shadow_baseline():
            raw = await _collect_recommendation(baseline_payload)
            return production_shadow.parse_counterfactual_baseline(
                raw, len(parsed_response)
            )

        return await production_shadow.run_trip_shadow(
            displayed_result,
            baseline_evaluator=_evaluate_shadow_baseline,
            production_route_id=f"candidate-{chosen_index}",
            production_status=advisor_status,
            candidate_summaries=production_shadow.safe_candidate_summaries(
                route_candidates
            ),
            source_counts=production_shadow.safe_source_counts(
                incidents=incidents,
                alert_count=len(relevant_alerts),
                stalled_train_count=len(stalled),
                stalled_bus_count=len(stalled_buses),
            ),
            incident_count=len(incidents),
            scan_status=str(incident_scan["scan_metadata"].get("status") or "failed"),
            snapshot_status="disabled",
            intelligence_latency_ms=round(
                _d("advisor", "incidents") * 1000
            ),
        )

    except HTTPException:
        raise
    except Exception:
        import traceback
        # Full detail goes to the server log only; the public 500 stays generic
        # so internal exception text is never exposed to the browser.
        print(f"[trip] UNHANDLED ERROR:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Trip planning failed")
    finally:
        if incident_task is not None and not incident_task.done():
            incident_task.cancel()
            try:
                await incident_task
            except asyncio.CancelledError:
                pass
        await admission.release(lease)


router.post("/api/trip/enrich-route")(enrich_route)
