# Frontend Library Contracts

`frontend/lib` contains client/server utilities that bridge SmartRoute to the
FastAPI backend. Some names look legacy but are still load-bearing contracts.

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
