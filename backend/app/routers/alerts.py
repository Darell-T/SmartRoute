# alerts.py - Service Alerts Endpoint
#
# GET /api/alerts?lines=G,B,D
#
# This file will contain:
# - Endpoint to fetch current MTA service alerts for specified subway lines
# - Workflow:
#   1. Parse lines query parameter
#   2. Fetch alerts from MTA GTFS-RT service alerts feed (cached in Redis)
#   3. Filter alerts relevant to requested lines
#   4. Call ai_advisor service to translate raw MTA alert text to plain English
#   5. Return list of translated alerts with severity levels
