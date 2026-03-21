import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.services.route_calculator import nearest_stops, possible_routes, get_schedule, combine_data

origin = "350 5th Ave, New York, NY 10118"
dest = "Union Square, New York, NY"

pipeline_start = time.time()

print("=== Step 1: Nearest Stops ===")
stops = nearest_stops(origin, dest)
print(f"Origin stops: {[s['stop_name'] for s in stops['origin_stops']]}")
print(f"Dest stops: {[s['stop_name'] for s in stops['dest_stops']]}")

print("\n=== Step 2: Possible Routes ===")
routes = possible_routes(stops)
for r in routes:
    print(f"  {r['origin_stop']} -> {r['dest_stop']} via {list(r['routes'])}")

print("\n=== Step 3: Schedule + Stalled Trains ===")
schedule = asyncio.run(get_schedule(routes))
print(f"Schedule updates: {len(schedule['user_schedule'])}")
print(f"Stalled trains: {len(schedule['stalled_trains'])}")
if schedule["stalled_trains"]:
    for t in schedule["stalled_trains"]:
        print(f"  STALLED: {t['route_id']} at {t['stop_id']} (status: {t['status']})")

print("\n=== Step 4: Combine Data ===")
combined = combine_data(routes, schedule, stops)
parsed = json.loads(combined)
print(f"Keys in payload: {list(parsed.keys())}")
print(f"Possible routes: {len(parsed['possible_routes'])}")
print(f"Schedule entries: {len(parsed['schedule_for_user_stops_only'])}")
print(f"Stalled trains: {len(parsed['stalled_trains_at_stops'])}")

print("\n=== Step 5: Incident Monitor (Grok) ===")
try:
    from app.services.incident_monitor import get_incidents
    station_names = [s["stop_name"] for s in stops["origin_stops"]] + [s["stop_name"] for s in stops["dest_stops"]]
    incidents = get_incidents(station_names)
    print(f"Incidents response: {incidents[:500]}")
except Exception as e:
    print(f"Grok skipped (expected if no XAI key): {e}")
    incidents = json.dumps({"incidents": []})

print("\n=== Step 6: AI Advisor (Claude) ===")
try:
    from app.services.ai_advisor import get_recommendation
    recommendation = get_recommendation(combined, incidents)
    print(f"\nJARVIS says:\n{recommendation}")
except Exception as e:
    print(f"Claude skipped (check ANTHROPIC_API_KEY): {e}")

pipeline_end = time.time()
print(f"\n=== Pipeline Complete in {pipeline_end - pipeline_start:.2f} seconds ===")
