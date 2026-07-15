"""Static venue/event data for the P1 tools (event_lookup, poi_search's
venue-adjacent name matching, venue_crowd_window).

Two tables, both hand-curated and deliberately not sourced live:

- `estimate_event_duration()`: crude event-length heuristics keyed by
  Ticketmaster classification strings (segment/genre/subGenre), used to turn
  an event's start time into an *estimated* end time. Never presented to the
  rider as an official schedule -- callers must label it an estimate.
- `VENUE_CROWD_TABLE`: which subway stations/lines see a post-event surge
  near a handful of major venues, and a plain-language alternate. This is a
  static table, not a live crowd sensor -- `venue_crowd_window.py` always
  sets `is_heuristic: true` on results derived from it.

Leaf module: no imports from sibling `agent` modules, so both `tools/`
modules and their tests can depend on it freely.
"""

from __future__ import annotations

from datetime import timedelta

# Post-event subway surge window, relative to the event's (estimated) end
# time -- crowds build a bit before the final whistle/encore and taper off
# over the following ~50 minutes as platforms clear. Same offsets for every
# venue in the table; only the affected stations/lines differ.
SURGE_START_OFFSET_MIN = -15
SURGE_END_OFFSET_MIN = 50

_DURATION_RULES: list[tuple[tuple[str, ...], timedelta, str]] = [
    (("nba",), timedelta(hours=2, minutes=30), "NBA game"),
    (("nhl",), timedelta(hours=2, minutes=30), "NHL game"),
    (("mlb", "baseball"), timedelta(hours=3), "MLB game"),
    (("nfl",), timedelta(hours=3, minutes=15), "NFL game"),
    (("soccer", "fifa", "football (soccer)"), timedelta(hours=2, minutes=15), "Soccer match"),
    (("music", "concert"), timedelta(hours=3), "Concert"),
]
_DEFAULT_DURATION = timedelta(hours=3)
_DEFAULT_LABEL = "Event"


def _format_duration(duration: timedelta) -> str:
    total_minutes = int(duration.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def estimate_event_duration(*classification_strings: str | None) -> tuple[timedelta, str]:
    """Match Ticketmaster classification text (segment/genre/subGenre, in any
    order) against the duration rules and return `(duration, basis_text)`,
    e.g. `(timedelta(hours=2, minutes=30), "NBA game ≈ 2h30m")`. Falls
    back to the 3-hour default for anything unrecognized."""
    haystack = " ".join(s for s in classification_strings if s).lower()
    for keywords, duration, label in _DURATION_RULES:
        if any(keyword in haystack for keyword in keywords):
            return duration, f"{label} ≈ {_format_duration(duration)}"
    return _DEFAULT_DURATION, f"{_DEFAULT_LABEL} ≈ {_format_duration(_DEFAULT_DURATION)}"


VENUE_CROWD_TABLE: dict[str, dict] = {
    "msg": {
        "stations": ["34 St-Penn Station"],
        "lines": ["1", "2", "3", "A", "C", "E"],
        "alternates": "walk ~5 min to Herald Sq (B/D/F/M/N/Q/R/W) or 28 St",
        "note": "",
    },
    "barclays": {
        "stations": ["Atlantic Av-Barclays Ctr"],
        "lines": ["2", "3", "4", "5", "B", "D", "N", "Q", "R", "W"],
        "alternates": "walk a few minutes to Nevins St (2/3/4/5) or Dean St (B/Q) for lighter platforms",
        "note": "",
    },
    "yankee_stadium": {
        "stations": ["161 St-Yankee Stadium"],
        "lines": ["4", "B", "D"],
        "alternates": "consider Mount Eden Av (4) one stop away, or Metro-North's Yankees-E 153 St station",
        "note": "",
    },
    "citi_field": {
        "stations": ["Mets-Willets Point"],
        "lines": ["7"],
        "alternates": "LIRR runs supplemental service from Mets-Willets Point right after games",
        "note": "",
    },
    "penn_station": {
        "stations": ["34 St-Penn Station"],
        "lines": ["1", "2", "3", "A", "C", "E"],
        "alternates": "walk ~5 min to Herald Sq (B/D/F/M/N/Q/R/W) if the Penn Station platforms are jammed",
        "note": (
            "NJ Transit and LIRR concourses also get very crowded here after "
            "MetLife Stadium events (Giants, Jets, FIFA matches)."
        ),
    },
    "port_authority": {
        "stations": ["42 St-Port Authority Bus Terminal"],
        "lines": ["A", "C", "E", "N", "Q", "R", "W", "S", "1", "2", "3", "7"],
        "alternates": "consider walking a block to 5 Av/53 St (E/M) or Times Sq-42 St for lighter platforms",
        "note": "",
    },
}

# Venue-name -> venue_key normalization, including common aliases/shorthand
# seen in Ticketmaster venue names and rider phrasing.
VENUE_ALIASES: dict[str, str] = {
    "madison square garden": "msg",
    "the garden": "msg",
    "msg": "msg",
    "barclays center": "barclays",
    "the barclays center": "barclays",
    "barclays": "barclays",
    "yankee stadium": "yankee_stadium",
    "citi field": "citi_field",
    "penn station": "penn_station",
    "pennsylvania station": "penn_station",
    "moynihan train hall": "penn_station",
    "port authority bus terminal": "port_authority",
    "port authority": "port_authority",
}


def normalize_venue_name(name: str | None) -> str | None:
    """Best-effort venue name -> venue_key lookup. Exact match first, then a
    substring check (Ticketmaster venue names often carry extra text like
    'Madison Square Garden, New York, NY'). Returns None for anything not in
    the table -- callers treat that as "no crowd heuristic available"."""
    if not name:
        return None
    key = " ".join(str(name).strip().lower().split())
    if not key:
        return None
    if key in VENUE_ALIASES:
        return VENUE_ALIASES[key]
    for alias, venue_key in VENUE_ALIASES.items():
        if alias in key:
            return venue_key
    return None
