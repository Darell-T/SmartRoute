# JARVIS — Wire In MTA Service Alerts

## Context

The app already fetches GTFS-RT trip updates and vehicle positions from MTA feeds in `app/services/mta_feed.py`. It uses `gtfs_realtime_pb2.FeedMessage` to parse protobuf bytes, with two parsers: `parse_bytes` (trip updates) and `parse_vehicle_positions` (vehicle positions).

There is a separate MTA GTFS-RT feed for **service alerts** (planned work, suspensions, shuttle replacements, reroutes) that we are not fetching or parsing. This means JARVIS has no idea when a train line is suspended or replaced by shuttle buses, and gives wrong recommendations.

**Alerts feed URL:** `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts`

This is a single endpoint (not split by route like the trip update feeds). It returns a standard GTFS-RT `FeedMessage` protobuf where entities have the `alert` field set instead of `trip_update` or `vehicle`.

## What to do

### Step 1 — Add alert fetching to `mta_feed.py`

Add a new async function `fetch_service_alerts()` that:
- Fetches the single alerts URL: `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts`
- Uses the same caching pattern as `fetch_feeds` (check `cache_get` first, fall back to HTTP fetch, `cache_set` with a 60 second TTL since alerts update less frequently)
- Returns the raw bytes

### Step 2 — Add alert parser to `mta_feed.py`

Add a new function `parse_service_alerts(rawBytes: bytes) -> list` following the same pattern as `parse_bytes` and `parse_vehicle_positions`.

A GTFS-RT alert entity looks like this:
```
entity {
  id: "..."
  alert {
    active_period {
      start: <unix timestamp>
      end: <unix timestamp>
    }
    informed_entity {
      route_id: "Q"
    }
    informed_entity {
      route_id: "Q"
      stop_id: "D28"
    }
    header_text {
      translation {
        text: "Q trains are suspended between ..."
        language: "en"
      }
    }
    description_text {
      translation {
        text: "Longer description of the service change..."
        language: "en"
      }
    }
  }
}
```

Parse each alert entity and return a list of dicts:
```python
{
    "alert_id": entity.id,
    "header": <english text from header_text.translation>,
    "description": <english text from description_text.translation>,
    "route_ids": <list of unique route_ids from informed_entity>,
    "stop_ids": <list of unique stop_ids from informed_entity>,
    "start": <unix timestamp from active_period, or None>,
    "end": <unix timestamp from active_period, or None>,
}
```

Notes:
- `informed_entity` is repeated (not a single field) -- iterate over `alert.informed_entity` to collect all route_ids and stop_ids.
- `header_text` and `description_text` contain a `translation` repeated field. Find the one with `language == "en"` or just take the first one.
- `active_period` can also be repeated. Take the first one if present.
- Some fields may be empty or missing. Handle gracefully.
- Filter to only currently active alerts: check that `now` falls within `active_period.start` and `active_period.end`. If `end` is 0 or missing, treat the alert as ongoing.

### Step 3 — Filter alerts by relevant routes

Add a helper function `filter_alerts_for_routes(alerts: list, route_ids: set) -> list` that takes the full parsed alerts list and a set of route IDs the user cares about, and returns only alerts where `route_ids` overlaps.

### Step 4 — Integrate into the trip pipeline

In `app/routers/trips.py`, in the `plan_trip` function:

1. Add `fetch_service_alerts` and `parse_service_alerts` to the imports from `mta_feed`.

2. In the `asyncio.gather` block that currently runs `get_schedule` and `safe_incidents` in parallel, add the alerts fetch:
```python
route_data = await asyncio.gather(
    get_schedule(route_options),
    safe_incidents(),
    fetch_service_alerts(),   # <-- add this
)
```

3. After the gather, parse and filter the alerts:
```python
user_schedule = route_data[0]
incident_reports = route_data[1]
raw_alerts = route_data[2]

service_alerts = parse_service_alerts(raw_alerts)
# Build set of route IDs the user might take
user_route_ids = set()
for option in route_options:
    user_route_ids.update(option["routes"])
relevant_alerts = filter_alerts_for_routes(service_alerts, user_route_ids)
```

4. Add the relevant alerts to `combined_data` so JARVIS can see them. After `combine_data(...)` is called, inject the alerts:
```python
# combined_data is a JSON string, so parse, add alerts, re-serialize
combined_dict = json.loads(combined_data)
combined_dict["service_alerts"] = relevant_alerts
combined_data = json.dumps(combined_dict)
```

### Step 5 — Update the JARVIS system prompt

In `app/services/ai_advisor.py`, update the `SYSTEM_PROMPT` to tell JARVIS about the new data field. Add this to the section that describes the JSON keys:

```
- "service_alerts": active MTA service alerts affecting the rider's possible routes. Each alert has a header and description explaining the service change (suspensions, shuttle buses, reroutes, planned work). These are authoritative -- if an alert says a line is suspended, do NOT recommend that line for the suspended segment. Suggest alternatives.
```

Also add this to the "Your job" section:

```
6. If a service alert indicates a line is suspended, partially suspended, or replaced by shuttle buses on the rider's route, do NOT recommend that line for the affected segment. Explain the disruption briefly and recommend the best alternative. This takes priority over schedule data -- a train may show scheduled arrivals even when service is suspended.
```

### Step 6 — Do NOT remove incident_monitor

The existing `incident_monitor.py` and `safe_incidents()` call should remain untouched. It serves a different purpose (real-world incidents like fires, police activity, etc. from social media). Service alerts and incidents are complementary data sources.

## Constraints

- Follow the exact coding patterns already in `mta_feed.py` -- same imports, same style, same caching approach.
- Do not modify `parse_bytes` or `parse_vehicle_positions`.
- Do not modify `fetch_feeds` or `route_to_feed`.
- Do not change the `stream_recommendation` function signature or the retry/backoff logic in `ai_advisor.py`.
- Test that the alerts feed URL is reachable and returns valid protobuf before assuming the parsing works.
