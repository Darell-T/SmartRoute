# Frontend library

`frontend/lib` contains client and server utilities that bridge SmartRoute to the
FastAPI backend. Some names look legacy but are still load-bearing contracts.

## Module map

- `agent-chat/` owns the browser agent chat pipeline, including SSE parsing, session persistence, turn control, route-card contracts, and `use-agent-chat`.
- `server/` owns Next route-handler proxies, rate limiting, and request principals that must never reach the browser bundle.
- `hooks/` owns React hooks that are not part of agent chat, including live feed, service alerts, destination search, theme, viewport, and voice input.
- `api.ts` owns browser trip-plan and route-enrichment fetches against the Next API routes.
- `canonical-itinerary-schema.ts` owns the zod schema for the server-owned canonical itinerary.
- `canonical-itinerary-label.ts` owns passenger-facing place and stop labels derived from canonical itinerary fields.
- `trip-response.ts` owns zod parsing of the trip-plan payload and the `TRIP_PLAN_FAILED` copy.
- `route-planning.ts` owns transit-step classification and canonical route-id derivation for display scoping.
- `response-presentation.ts` owns Auto and Quick presentation-mode persistence for chat.
- `smart-route.ts` owns formatting of server-owned route summary facts without inventing durations.
- `initial-geolocation.ts` owns first-load NYC geolocation resolution and service-area fallback.
- `live-feed-connection.ts` owns the WebSocket connection lifecycle for the live feed.
- `service-alert-poll.ts` owns the gate that ignores poll results after a service-alert websocket epoch opens.
- `ws-ticket.ts` owns browser ticket fetch and WebSocket URL construction for live sockets.
- `mapbox-search.ts` owns Mapbox destination suggest and retrieve calls parsed at the response boundary.
- `mta-colors.ts` owns official MTA route color lookup from `mta-colors.json`.
- `nyc-route-clock.ts` owns America/New_York formatting of server-owned ISO timestamps.
- `schema-primitives.ts` owns shared zod text, number, and list primitives.
- `utils.ts` owns `cn` class merging and `clamp`.
- `artifact-manifest.json` is the generated transit artifact digest consumed by the map.
- `mta-colors.json` is the generated official MTA route color table.

## Backend Fallback URL

`PROD_API_FALLBACK` currently points at
`https://jarvis-mta-assistant.onrender.com`. That hostname is the deployed
backend service, so do not rename it in code without a coordinated backend,
hosting, and environment migration. `API_URL` and `NEXT_PUBLIC_API_URL` still
override it when configured.

## APP_KEY

Server-side Next routes use `APP_KEY` to authenticate requests to FastAPI and
to mint short-lived WebSocket tickets. Never expose it as a `NEXT_PUBLIC_*`
value. Browser code should request `/api/ws-ticket` and use the returned ticket
instead.
