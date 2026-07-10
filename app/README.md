# Hook AI — internal plane (app)

Runnable scaffold of the **internal agent plane**: an orchestrator routing to
six specialists over one canonical `Profile`. LLM calls go through an
OpenAI-compatible client pointed at **OpenRouter**.

This is the standalone half of the design — it works with no CROO dependency.
The CROO marketplace plane is deliberately deferred (its developer SDK isn't
public yet; see the architecture doc).

## Layout

```
app/
├── requirements.txt        openai + python-dotenv (that's all)
├── .env.example            copy to .env, add your OpenRouter key
├── demo.py                 end-to-end run (needs a key)
├── smoke_test.py           offline wiring test (no key, no network)
└── hookai/
    ├── config.py           env/.env loading (OpenRouter)
    ├── llm.py              OpenRouterLLM + FakeLLM + JSON parsing
    ├── profile.py          the canonical Profile data model
    ├── gates.py            approval gates (AutoBlock / Console / ApproveAll)
    ├── orchestrator.py     wires specialists + tools + gate
    ├── agents/             the 6 specialists (one file each)
    └── tools/              connectors: job_data, enrichment, email, docgen (stubs)
```

## Run it

```bash
cd app
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# offline — verifies wiring + that the gate blocks outward actions (no key needed)
python smoke_test.py

# live — runs the full pipeline through OpenRouter
cp .env.example .env        # then edit .env and set OPENROUTER_API_KEY
python demo.py
```

### Configuring OpenRouter

Put your key in `.env` (git-ignored):

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=openai/gpt-4o-mini   # pick a CURRENT slug at https://openrouter.ai/models
```

`OPENROUTER_MODEL` is just a string passed straight to the API. The default is a
*placeholder* — model availability changes, so if you get a model error, set this
to a live slug from openrouter.ai/models. Optional vars (base URL, attribution
headers, temperature) are documented in `.env.example`.

## What's real vs. stubbed

| Part | State |
|------|-------|
| Orchestrator, 6 specialists, Profile, gates | **Real** — fully wired |
| CV Tailor, Match & Rank, Skills Advisor, Application/Outreach drafting | **Real LLM** via OpenRouter |
| Job discovery (`tools/job_data.py`) | **Adzuna live API implemented** (set `ADZUNA_*` in `.env`); falls back to **stub** sample postings without keys |
| Contact enrichment (`tools/enrichment.py`) | **Stub** fake contact → swap for Hunter.io/Apollo (public emails) |
| Email sending (`tools/email.py`) | **Stub** logs only, never sends → swap after compliance review |
| Application *submit* / email *send* | **Gated** — `AutoBlockGate` blocks; a human approves |

## The one rule baked into the architecture

Only two specialists (`ApplicationAssistant`, `Outreach`) take outward actions,
and both must clear an `ApprovalGate`. The default gate **blocks and queues** —
so the agent can *prepare* an application or email autonomously, but a human
authorizes the actual submit/send. That's the "assisted, not autonomous" design
stance. Swap in `ConsoleApprovalGate` for an interactive y/N prompt.
