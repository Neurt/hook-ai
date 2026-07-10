# Hook AI — running the stack & deploying to CROO

## Architecture

```
 Browser ──HTTP──▶ nginx (web :8080) ──/api──▶ FastAPI (api :8000) ──▶ hookai orchestrator
                                                      │                 (OpenRouter · Adzuna · Hunter)
                                                      ▼
                                          /internal/fulfill  ◀───────────┐
                                                                          │
 CROO network  ◀──@croo-network/sdk (WebSocket)──  croo-connect (Node provider)
```

- **web** — static chat UI (nginx), proxies `/api` to the backend.
- **api** — FastAPI wrapping the Hook AI agents (chat + `/internal/fulfill`).
- **croo-connect** — optional Node provider that connects the backend to CROO.

## Run it locally

```bash
# from the repo root (keys live in app/.env — OPENROUTER_API_KEY required)
docker compose up --build
# open http://localhost:8080
```

Load your CV in the left panel, then chat: *"recommend roles"*, *"find data analyst jobs in
London"*, *"make my CV ATS-friendly"*, *"draft an email to #1"*.

Stop with `docker compose down`. The API reads `app/.env`; `ADZUNA_*` and `HUNTER_API_KEY`
are optional (job search / real contact lookup degrade to stubs without them).

## Deploying to CROO — the format

CROO = the **CROO Agent Protocol** on **crew.network** (a decentralized agent marketplace on
**Base**). Humans use the store at agent.crew.network; agents connect over **MCP / JSON-RPC**
at `mcp://crew.network`. You keep your agent local and connect it.

**Verified from the dashboard (agent.crew.network):**
- Auth env var: `CROO_API_KEY=croo_sk_...`
- Network: `CROO_MCP_URL=mcp://crew.network` (MCP `tools/list`: marketplace.search, agent.spawn, task.fund, settle.preview, wallet.balance, …)
- Register the agent + service at **https://agent.crew.network** (API key shown once).
- Start the provider (Node, from the SDK): `npx ts-node examples/provider.ts`
- Service schema (see [`croo-connect/croo.service.json`](croo-connect/croo.service.json)):
  `name, description, price (USDC), sla_hours, sla_minutes, deliverable_type (text|schema),
  requirements_type (text|schema|unset)` + 1–5 skill tags.
- Order lifecycle: **Negotiate → Lock (escrow) → Deliver (output + proof: result hash /
  execution log / attestation) → Clear (auto-verify → settlement + reputation)**.

**Not yet public (so it's scaffolded, not fabricated):** the exact `@croo-network/sdk`
method/event names. They're marked `[VERIFY]` in [`croo-connect/provider.mjs`](croo-connect/provider.mjs);
confirm against the SDK reference / `examples/provider.ts`, then fill in the wiring. The
fulfilment path (call `/internal/fulfill`, hash the result for the delivery proof) is complete.

**Run the provider:**
```bash
cp croo-connect/.env.example croo-connect/.env   # set CROO_API_KEY
docker compose --profile croo up --build         # api + web + croo-connect
```
Without the SDK installed it runs in **dry-run** (logs the service it would offer).

## How it maps to CROO's model
Hook AI is the **sovereign local runtime** (data + execution stay yours). `croo-connect` is
the **CAP adapter**: it advertises the service, receives orders over WebSocket, fulfils them
via the backend, and returns deliverables with a proof so CROO can verify, settle (USDC on
Base), and update reputation. One CROO order in → one Hook AI deliverable out.
