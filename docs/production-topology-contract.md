# SmartRoute production topology contract

Version: `2026-07-27.1`

This repository supports one backend application worker process. That process
owns one process-local 511NY poller and snapshot. Do not increase backend worker
count or enable a second poller until snapshot/poller coordination moves to a
shared store and this contract is revised.

Required production environment:

- `APP_KEY` for protected API and WebSocket ticket verification.
- `ANTHROPIC_API_KEY` and configured Auto/Quick model names when agent chat is enabled.
- `REDIS_URL` for durable agent sessions. `AGENT_ALLOW_MEMORY_SESSIONS` must be unset or `0`.
- `CORS_ORIGIN_REGEX` only when preview-origin support is intended.

Startup must run exactly one FastAPI worker process. The operator must record
the concrete platform command, worker count, Redis service/durability, and
health-check target in the release record; those values are not inferable from
this repository.

Probe `/health` for process liveness. Probe `/ready` for traffic admission: it
returns `503` until application startup completes or durable chat sessions are
unavailable. It intentionally does not make optional database, MTA, 511NY, or
model-provider availability a startup fatality.

Release procedure:

1. Record the immutable Git SHA and verify the intended file list.
2. Deploy with the declared single-worker topology and required environment.
3. Verify `/health` and `/ready`, then a bounded authenticated chat/session
   continuity smoke.
4. Confirm only one poller starts for the process and record the evidence.
5. If readiness or smoke fails, route traffic back to the previously recorded
   healthy SHA; do not roll forward with memory-only chat sessions.

External evidence still required: platform worker settings, Redis
durability/connectivity, configured health probe, deploy logs showing one
poller, a staging smoke, and the platform rollback result.
