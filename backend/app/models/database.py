# database.py - SQLAlchemy Database Models
#
# This file will contain:
# - SQLAlchemy async engine and session configuration
# - Database models:
#
# DelayObservation:
#   - id: Primary key
#   - line: Subway line (G, A, 1, etc.)
#   - station: Station name
#   - day_of_week: 0-6 (Monday-Sunday)
#   - hour: 0-23
#   - reported_delay: Delay reported by MTA feed (minutes)
#   - actual_delay: Actual observed delay (minutes)
#   - timestamp: When observation was recorded
#
# Trip:
#   - id: UUID primary key
#   - origin_lat, origin_lng, origin_name
#   - destination_lat, destination_lng, destination_name
#   - arrive_by: Target arrival time
#   - created_at: When trip was planned
#   - route_data: JSON blob of calculated route
#   - is_active: Whether trip is being monitored
#
# Stop (from GTFS static):
#   - stop_id: MTA stop ID
#   - stop_name: Human-readable name
#   - stop_lat, stop_lon: Coordinates
#   - parent_station: For linking platforms to stations
