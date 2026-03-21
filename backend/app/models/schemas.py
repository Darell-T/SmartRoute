# schemas.py - Pydantic Request/Response Models
#
# This file will contain Pydantic models matching the API contract:
#
# Request Models:
# - Location: lat, lng, name
# - TripRequest: origin (Location), destination (Location), arrive_by (datetime)
#
# Response Models:
# - RouteLeg: type (transit|transfer|walk), line, from, to, duration_min, delay_min
# - Route: legs (list[RouteLeg]), total_duration_min
# - Alternative: summary, leave_by, estimated_arrival, confidence
# - Recommendation: leave_by, estimated_arrival, status (on_time|cutting_it_close|late),
#                   confidence, explanation
# - TripResponse: recommendation, route, alternatives
#
# Alert Models:
# - ServiceAlert: line, title, description, severity, translated_text
# - AlertsResponse: alerts (list[ServiceAlert])
