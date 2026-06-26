from __future__ import annotations

from datetime import timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    NYC_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    # Minimal fallback for Windows/dev environments that do not have tzdata
    # installed. Deployments with tzdata keep DST-aware ZoneInfo behavior.
    NYC_TZ = timezone(timedelta(hours=-5), "America/New_York")

BASE_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"
ALERTS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"
BUS_URL = "https://bustime.mta.info/api/siri/vehicle-monitoring.json"
BUS_STOPS_FOR_LOCATION_URL = "https://bustime.mta.info/api/where/stops-for-location.json"
BUS_STOP_MONITORING_URL = "https://bustime.mta.info/api/siri/stop-monitoring.json"

route_to_feed = {
    "A": "ace", "C": "ace", "E": "ace",
    "B": "bdfm", "D": "bdfm", "F": "bdfm", "M": "bdfm",
    "G": "g",
    "J": "jz", "Z": "jz",
    "N": "nqrw", "Q": "nqrw", "R": "nqrw", "W": "nqrw",
    "L": "l",
    "1": "", "2": "", "3": "", "4": "", "5": "", "6": "", "7": "",
    "S": "", "FS": "bdfm", "GS": "", "H": "ace",
    "SI": "si",
}

MTA_COLORS = {
    "A": "#0039A6", "C": "#0039A6", "E": "#0039A6",
    "B": "#FF6319", "D": "#FF6319", "F": "#FF6319", "M": "#FF6319", "FX": "#FF6319",
    "G": "#6CBE45",
    "J": "#996633", "Z": "#996633",
    "L": "#A7A9AC",
    "N": "#FCCC0A", "Q": "#FCCC0A", "R": "#FCCC0A", "W": "#FCCC0A",
    "1": "#EE352E", "2": "#EE352E", "3": "#EE352E",
    "4": "#00933C", "5": "#00933C", "6": "#00933C", "6X": "#00933C",
    "7": "#B933AD", "7X": "#B933AD",
    "S": "#808183", "FS": "#808183", "GS": "#808183", "H": "#808183",
    "SI": "#00A9CE",
}


def get_route_color(route_id: str) -> str:
    return MTA_COLORS.get((route_id or "").upper(), "#808183")


ALL_SUBWAY_ROUTES = list(route_to_feed.keys())
