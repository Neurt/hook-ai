# Hook AI — Job-Search Agent on CROO

> An AI agent that takes a candidate from raw CV to submitted application — ATS-ready
> resumes, live job matches, tailored applications, and hiring-team outreach — and sells
> those capabilities to humans **and other agents** as paid CAP services on the
> [CROO Agent Store](https://agent.croo.network).

Built for the **CROO Agent Hackathon** (tracks: Research & Intelligence · Open A2A).
License: MIT. Demo video: see the BUIDL page.

---

## What it does

| # | Pillar | Live capability |
|---|--------|-----------------|
| 1 | **CV → ATS** | Any CV (PDF / photo / DOCX / pasted text, any language incl. CJK) → clean ATS resume; per-job tailoring with fit score; PDF/DOCX export |
| 2 | **Find jobs + assisted apply** | Live international search (Jooble → Adzuna → self-hosted meta-search fallback chain), semantic ranking, freshness sort; application package (fields, screening answers, cover note) + Playwright form-fill CLI — **the human always clicks submit** |
| 3 | **Contact the hiring team** | Public hiring email discovered from the posting itself or the company's public pages (provenance kept); personalized draft; one-click `mailto:` handoff — **sent from the user's own mail client** |
| 4 | **Skills & certifications** | Ranked skill gaps + real, verifiable industry certifications (issuer + official site) |

Compliance is a design feature: no LinkedIn/social scraping, no autonomous applying,
no automated sending, provenance on every discovered contact, approval gates in code.

---

## Architecture

```
┌── web (React + TS + Tailwind, nginx) ─────┐
│   chat · attachments · CV cards · export  │
└──────────────┬────────────────────────────┘
               │ /api (REST)
┌── api (FastAPI, Python 3.13) ─────────────┐        ┌── CROO Agent Store ──┐
│   ONE Orchestrator "brain"                │        │  agent.croo.network  │
│   CV Tailor · Job Scout · Match&Rank      │        └──────────┬───────────┘
│   Application Assistant · Outreach        │                   │ WebSocket (CAP)
│   Skills Advisor · approval gates         │◄── /internal/ ────┤
└──────┬──────────────┬─────────────────────┘    fulfill   ┌────┴────────────┐
   SearXNG         SQLite                                  │  croo-connect   │
 (self-hosted    (sessions)                                │  provider.mjs   │
  meta-search)                                             └─────────────────┘
```

The same Orchestrator backs both doors: the human chat UI and the CROO order path.

---

## CAP / CROO integration (SDK methods used)

`croo-connect/provider.mjs` — the provider (seller) loop, `@croo-network/sdk`:

- `new AgentClient({ baseURL, wsURL, rpcURL }, CROO_SDK_KEY)`
- `client.connectWebSocket()` → `EventStream`
- `EventType.NegotiationCreated` → `client.getNegotiation(id)` (read buyer requirements) → `client.acceptNegotiation(id)` (on-chain order created)
- `EventType.OrderPaid` (USDC escrowed in CAPVault) → POST `/internal/fulfill` on the Hook AI backend → `client.deliverOrder(orderId, { deliverableType: Schema, deliverableSchema })` (deliverable hash on-chain, settlement to the agent's AA wallet)
- Restart-safe: if requirements aren't cached, `client.getOrder(orderId)` → `getNegotiation(order.negotiationId)`
- `EventType.OrderCompleted` → settled ✓

`croo-connect/requester.mjs` — the buyer (demo / A2A counterparty):

- `client.negotiateOrder({ serviceId, requirements })` (requirements = JSON string: `{task, cv_text, params}`)
- `EventType.OrderCreated` → `client.payOrder(orderId)` (auto-handles USDC approve)
- `EventType.OrderCompleted` → `client.getDelivery(orderId)` → parsed schema deliverable

**Integration notes:** CROO order requirements map 1:1 onto Hook AI's internal
capability API (`/internal/fulfill`): `{task: recommend|certs|find_jobs|ats|tailor|draft_email|prepare_application, cv_text, params}` — see `croo-connect/croo.service.json`
for the service schema mirrored in the dashboard. Deliverables are structured JSON
(`deliverable_type: schema`) with a sha256 result proof embedded. Oversized inputs
(>200KB CV text) return a clean `{"error": ...}` deliverable instead of hanging a paid
order. Gas is platform-sponsored; settlement is USDC on Base.

---

## Setup

Prereqs: Docker + Docker Compose; an [OpenRouter](https://openrouter.ai/keys) key;
optional free job-data keys ([Jooble](https://jooble.org/api/about),
[Adzuna](https://developer.adzuna.com/)).

```bash
# 1) configure
cp app/.env.example app/.env            # add OPENROUTER_API_KEY (+ optional job keys)
cp croo-connect/.env.example croo-connect/.env   # add CROO_SDK_KEY to go live on CROO

# 2) run the product (chat UI at http://localhost:8080)
docker compose up --build

# 3) run as a CROO provider (after registering at agent.croo.network)
docker compose --profile croo up --build
```

Sell/buy loop without Docker (Node 18+):

```bash
cd croo-connect && npm install
npm start                                # provider: accept + fulfil + deliver
CROO_SDK_KEY=croo_sk_<buyer> CROO_TARGET_SERVICE_ID=<id> npm run buy   # demo buyer
```

Tests (77, stdlib only, no network):

```bash
cd app && python -m unittest discover -s tests
```

CROO dashboard steps (one-time): register the agent at
[agent.croo.network](https://agent.croo.network) → copy the `croo_sk_...` key (shown
once) → add a Service matching `croo-connect/croo.service.json` (Deliverable: Schema,
Requirements: Schema).

---

## Repo layout

```
app/            FastAPI backend + hookai agent core (orchestrator, agents, tools, tests)
web/            React chat UI (Vite + TS + Tailwind, nginx)
croo-connect/   CAP provider + requester (Node, @croo-network/sdk)
cv-playground/  CLI tools (incl. Playwright assisted-apply)
searxng/        self-hosted meta-search config (keyless job/contact discovery)
```
