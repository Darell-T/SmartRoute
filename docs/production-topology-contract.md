# SmartRoute production topology contract

Version: `2026-07-28.1`

This document records the deployment behavior implemented by the application.
It does not declare a platform worker count: no deployment manifest in this
repository configures one.

## Environment and deployment matrix

| Concern | Production contract | Local/test or non-chat behavior |
|---|---|---|
| Runtime profile | Set `SMARTROUTE_ENV=production`. Unknown profiles never authorize mocks. | Use an explicit local/test profile for deterministic mock modes. |
| Server authentication | `APP_KEY` is required at backend startup and is shared only by FastAPI and server-side Next.js proxy/ticket routes. | Use a placeholder local secret; never expose it to browser code. |
| Chat sessions and admission | `REDIS_URL` is required and must be reachable. Chat, WebSocket ticket nonce replay protection, and admission leases are shared through Redis; absence or failure is a bounded `503`. | Redis is optional only for local/test or non-chat work. `AGENT_ALLOW_MEMORY_SESSIONS=1` is a local/test-only, non-durable escape hatch. |
| Optional providers and data paths | Database, MTA, 511NY, and model-provider availability do not make startup fail. Their rider-facing degradation remains explicit. | The same optional-path behavior applies. |
| Worker/poller behavior | Each FastAPI process starts its own 511NY poller and process-local snapshot. Redis shares chat/admission state, not 511NY snapshots. Record the platform worker configuration and monitor poller activity. | The same process-local ownership applies. |
| Health probes | `/health` is process liveness. `/ready` requires completed startup plus a durable, reachable Redis store for production chat traffic. | `/ready` may report `chat_sessions: local` only with the explicit local/test memory-session escape hatch. |

Text-to-speech is not a current product configuration. Do not set or document
legacy TTS environment variables; dependency cleanup is tracked separately.

## Release procedure

1. Record the immutable Git SHA, platform worker configuration, Redis service
   durability, and health-check target.
2. Deploy with the required production environment from the matrix.
3. Verify `/health` and `/ready`, then run a bounded authenticated chat/session
   continuity smoke.
4. Inspect deploy logs for the process-local poller behavior appropriate to the
   recorded platform topology.
5. If readiness or smoke fails, route traffic back to the previously recorded
   healthy SHA; do not roll forward with memory-only chat sessions.

External evidence remains required: platform topology settings, Redis
durability/connectivity, configured probes, deploy logs, a staging smoke, and
the platform rollback result.
