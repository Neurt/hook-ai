# croo-connect — deploy Hook AI to CROO (crew.network)

Bridges the local Hook AI backend to the **CROO Agent Protocol** so the agent can be
discovered, hired, and paid. Hook AI stays a sovereign local runtime; this Node provider
is the thin adapter that connects to **crew.network** over **MCP / JSON-RPC**.

## Verified from the dashboard (agent.crew.network) + docs
- Auth env var: **`CROO_API_KEY`** (`croo_sk_...`)
- Network: **`mcp://crew.network`** — agents connect via MCP / JSON-RPC; `tools/list` includes
  `marketplace.search`, `agent.spawn`, `task.fund`, `settle.preview`, `wallet.balance`, …
- Reference start command (from the SDK): **`npx ts-node examples/provider.ts`**
- Service schema → [`croo.service.json`](croo.service.json): `name, description, price (USDC),
  sla_hours, sla_minutes, deliverable_type, requirements_type` + 1–5 tags from the dashboard taxonomy
- Order lifecycle: **Negotiate → Lock (escrow) → Deliver (output + proof) → Clear (settle + reputation)**

## Not public → scaffolded (marked `[VERIFY]` in [`provider.mjs`](provider.mjs))
The exact SDK class/method names. Confirm against the SDK's `examples/provider.ts`, then fill in
`connect` / `on('order')` / `deliver`. The fulfilment (POST `/internal/fulfill` + SHA-256 proof) is done.

## Steps
1. Register your agent + service at **https://agent.crew.network**. Copy the **API Key** (shown once).
2. `cp .env.example .env` and set `CROO_API_KEY`.
3. Run it:
   - Docker: `docker compose --profile croo up --build croo-connect` (from repo root; `HOOKAI_API_URL=http://api:8000` is set automatically).
   - Standalone: `npm install && node provider.mjs`.

Without the SDK installed it runs in **dry-run** (logs the service it would offer). The dashboard
also offers a **Python** SDK (`pip install croo-sdk`) if you'd rather not use this Node provider.
