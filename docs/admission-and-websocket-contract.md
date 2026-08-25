# Admission and WebSocket Contract

Provider-backed trip planning, agent turns, and WebSocket connections pass
through one Redis-backed admission service. It atomically applies per-principal
and global request windows plus per-principal and global active-connection
leases. Chat and WebSocket leases remain held until their stream closes or is
cancelled. Redis absence or failure is a bounded `503` in Render/production;
the deterministic in-memory implementation is available only under an explicit
local/test runtime profile.

The browser never sends an address to FastAPI. On Vercel, the Next server uses
only the platform-injected `x-vercel-forwarded-for` value, HMACs it with
`APP_KEY`, and sends the resulting bounded opaque `X-SmartRoute-Principal`
alongside the already-required `X-App-Key`. FastAPI trusts that header only on
this authenticated server-to-server path, rejects it if missing or malformed,
and never logs or stores the platform identity. Deployments must not substitute
browser-provided `Forwarded`, `X-Forwarded-For`, or `X-Real-IP` headers. A
missing platform identity fails closed on Vercel.

WebSocket tickets are short-lived `exp.nonce.principal.signature` values. The
signature binds the ticket to its path and principal; FastAPI atomically records
the nonce in Redis before `accept`, so a replay, a parallel replay race, or a
different path is rejected. Each reconnect obtains a new ticket.
