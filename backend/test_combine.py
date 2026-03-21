import asyncio
import json
from app.services.route_calculator import nearestStops, possibleRoutes, getSchedule, combine_data

stops = nearestStops("350 5th Ave, New York", "1 Times Square, New York")
routes = possibleRoutes(stops)
schedule = asyncio.run(getSchedule(routes))

result = combine_data(routes, schedule, stops)
parsed = json.loads(result)

print(json.dumps(parsed, indent=2)[:3000])
print(f"\n... truncated. Total keys: {list(parsed.keys())}")
print(f"Possible routes: {len(parsed['possible_routes'])}")
print(f"Schedule updates: {len(parsed['schedule_for_user_stops_only'])}")
