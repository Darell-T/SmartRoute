import os
import httpx
from datetime import datetime, timezone
from zoneinfo import ZoneInfo



ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
key = (os.getenv("GOOGLE_ROUTES_API_KEY") or "").strip()
GOOGLE_ROUTES_TIMEOUT_S = float(os.getenv("GOOGLE_ROUTES_TIMEOUT_S", "12.0"))
GOOGLE_ROUTES_RETRIES = max(1, int(os.getenv("GOOGLE_ROUTES_RETRIES", "2")))
GOOGLE_ROUTES_ALTERNATIVES = os.getenv("GOOGLE_ROUTES_ALTERNATIVES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

ALLOWED_TRAVEL_MODES = {"SUBWAY", "BUS", "RAIL", "TRAIN", "LIGHT_RAIL"}
ALLOWED_ROUTING_PREFERENCES = {"FEWER_TRANSFERS", "LESS_WALKING"}

FIELD_MASK = ",".join([
    "routes.legs.steps.transitDetails",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.startLocation",
    "routes.legs.steps.endLocation",
    "routes.legs.duration",
    "routes.legs.distanceMeters",
    "routes.legs.steps.staticDuration",
    "routes.legs.polyline.encodedPolyline",
    "routes.legs.steps.polyline.encodedPolyline",
])


class GoogleRoutesError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider_status: int | None = None,
        provider_summary: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.provider_status = provider_status
        self.provider_summary = provider_summary


def _provider_error_summary(response) -> str:
    try:
        data = response.json()
    except Exception:
        text = getattr(response, "text", "") or ""
        return " ".join(text.split())[:300]

    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        parts = [
            str(error.get("status") or "").strip(),
            str(error.get("message") or "").strip(),
        ]
        return " ".join(part for part in parts if part)[:300]
    return ""


def _duration_to_minutes(value) -> int | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = float(value[:-1])
    except ValueError:
        return None
    return max(1, round(seconds / 60))


def _duration_to_seconds(value) -> int | None:
    """Parse the provider duration without losing sub-minute precision."""
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        seconds = float(value[:-1])
    except ValueError:
        return None
    return max(0, int(round(seconds)))

def _serialize_departure_time(value: str | datetime) -> str:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GoogleRoutesError(
                "invalid_departure_time",
                f"unparseable departure_time: {value!r}",
            ) from exc
    else:
        raise GoogleRoutesError(
            "invalid_departure_time",
            f"unsupported departure_time type: {type(value).__name__}",
        )

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise GoogleRoutesError(
            "invalid_departure_time",
            "departure_time must be timezone-aware (RFC3339 with offset)",
        )

    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


async def get_transit_route(
    origin: tuple,
    dest: str,
    dest_coords: tuple | None = None,
    *,
    allowed_travel_modes: list[str] | None = None,
    routing_preference: str = "FEWER_TRANSFERS",
    departure_time: str | datetime | None = None,
) -> dict:
    if not key:
        raise GoogleRoutesError("not_configured", "Google Routes API is not configured")

    if allowed_travel_modes is None:
        allowed_travel_modes = ["SUBWAY", "BUS"]
    if not allowed_travel_modes or any(
        mode not in ALLOWED_TRAVEL_MODES for mode in allowed_travel_modes
    ):
        raise GoogleRoutesError(
            "invalid_modes",
            f"invalid allowed_travel_modes: {allowed_travel_modes!r}",
        )

    if routing_preference not in ALLOWED_ROUTING_PREFERENCES:
        raise GoogleRoutesError(
            "invalid_preference",
            f"invalid routing_preference: {routing_preference!r}",
        )

    departure_time_str = (
        _serialize_departure_time(departure_time) if departure_time is not None else None
    )

    destination = (
        {
            "location": {
                "latLng": {
                    "latitude": dest_coords[0],
                    "longitude": dest_coords[1],
                }
            }
        }
        if dest_coords
        else {"address": dest}
    )
    request_body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin[0],
                    "longitude": origin[1]
                }
            }
        },
        "destination": destination,
        "travelMode": "TRANSIT",
        "computeAlternativeRoutes": GOOGLE_ROUTES_ALTERNATIVES,
        "transitPreferences": {
            "allowedTravelModes": allowed_travel_modes,
            "routingPreference": routing_preference
        },
        "languageCode": "en-US"
    }
    if departure_time_str is not None:
        request_body["departureTime"] = departure_time_str
    headers = {
        "Content-type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELD_MASK
    }

    # Transit computeRoutes with alternatives can be slow. Keep the budget
    # configurable so local route planning fails fast instead of exhausting
    # the whole ATLAS interaction window before downstream fallbacks can run.
    async with httpx.AsyncClient() as client:
        last_exc: Exception | None = None
        for attempt in range(1, GOOGLE_ROUTES_RETRIES + 1):
            try:
                response = await client.post(
                    ROUTES_URL,
                    json = request_body,
                    headers = headers,
                    timeout = GOOGLE_ROUTES_TIMEOUT_S,
                )
                response.raise_for_status()
                try:
                    return response.json()
                except (ValueError, TypeError) as exc:
                    print(f"[directions] Google Routes invalid JSON: {type(exc).__name__}")
                    raise GoogleRoutesError(
                        "invalid_json",
                        "Google Routes API returned invalid JSON",
                    ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                print(f"[directions] Google Routes timeout (attempt {attempt})")
            except httpx.HTTPStatusError as exc:
                # Non-2xx (bad/expired key, quota, provider outage). Do not retry;
                # surface a clean upstream error and keep provider details out of
                # the public response.
                status_code = exc.response.status_code
                summary = _provider_error_summary(exc.response)
                print(
                    "[directions] Google Routes HTTP "
                    f"{status_code} code=http_{status_code} summary={summary or 'none'}"
                )
                raise GoogleRoutesError(
                    f"http_{status_code}",
                    f"Google Routes API error {status_code}",
                    provider_status=status_code,
                    provider_summary=summary,
                ) from exc
            except httpx.RequestError as exc:
                print(f"[directions] Google Routes request failed: {type(exc).__name__}")
                raise GoogleRoutesError(
                    "request_failed",
                    "Google Routes API request failed",
                ) from exc
        raise GoogleRoutesError("timeout", "Google Routes API timed out") from last_exc

def parse_response(response: dict) -> list:
    # Defensive: a malformed/empty provider route (missing legs, partial transit
    # details, bad timestamps) skips that one route rather than crashing the whole
    # trip. An entirely empty/garbage response yields [].
    routes = []
    for route in response.get("routes", []):
        legs = route.get("legs") or []
        if not legs:
            continue
        try:
            routes.append(_parse_leg_steps(legs[0]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            print(f"[directions] skipping malformed route: {exc!r}")
    return routes


def _parse_leg_steps(leg: dict) -> list:
    steps = []
    route_total_minutes = _duration_to_minutes(leg.get("duration"))
    route_total_seconds = _duration_to_seconds(leg.get("duration"))
    for step in leg.get("steps", []):

            if step["travelMode"] == "TRANSIT":
                route_id = step["transitDetails"]["transitLine"]["nameShort"].replace("Line", "").strip()
                line_color = step["transitDetails"]["transitLine"]["color"]
                direction = step["transitDetails"]["headsign"]
                stop_count = step["transitDetails"]["stopCount"]

                departure_stop = step["transitDetails"]["stopDetails"]["departureStop"]["name"]
                arrival_stop = step["transitDetails"]["stopDetails"]["arrivalStop"]["name"]

                departure_coords = step["transitDetails"]["stopDetails"]["departureStop"]["location"]["latLng"]
                arrival_coords = step["transitDetails"]["stopDetails"]["arrivalStop"]["location"]["latLng"]


                depart_time = step["transitDetails"]["stopDetails"]["departureTime"]
                arrival_time = step["transitDetails"]["stopDetails"]["arrivalTime"]



                departure_utc = datetime.fromisoformat(depart_time.replace("Z", "+00:00"))
                departure_est = departure_utc.astimezone(ZoneInfo("America/New_York"))

                arrival_utc = datetime.fromisoformat(arrival_time.replace("Z", "+00:00"))
                arrival_est = arrival_utc.astimezone(ZoneInfo("America/New_York"))
                minutes_until_train_arrives = (departure_est - datetime.now(ZoneInfo("America/New_York"))).total_seconds() / 60
                arrival = (arrival_est - datetime.now(ZoneInfo("America/New_York"))).total_seconds() / 60

                transit_step = {
                    "type": step["transitDetails"]["transitLine"]["vehicle"]["type"],
                    "route_id": route_id,
                    "train_line": route_id,
                    "line_color": line_color,
                    "direction": direction,
                    "stop_count": stop_count,
                    "departure_stop": departure_stop,
                    "arrival_stop": arrival_stop,
                    "departure_coords": departure_coords,
                    "arrival_coords": arrival_coords,
                    "minutes_until_train_arrives": minutes_until_train_arrives,
                    "minutes_until_arrival": arrival,
                    "departure_time_iso": departure_est.isoformat(),
                    "arrival_time_iso": arrival_est.isoformat(),
                    "route_total_minutes": route_total_minutes,
                    "route_total_seconds": route_total_seconds,
                    "polyline": step["polyline"]

                }
                steps.append(transit_step)
            
            if step["travelMode"] == "WALK":
                steps.append({
                    "type": step["travelMode"],
                    "start_point": step["startLocation"]["latLng"],
                    "end_point": step["endLocation"]["latLng"],
                    "route_total_minutes": route_total_minutes,
                    "route_total_seconds": route_total_seconds,
                    "polyline": step["polyline"],
                })
    return steps
