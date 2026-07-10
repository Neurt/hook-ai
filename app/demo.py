"""End-to-end demo of the internal plane.

Runs the full pipeline against OpenRouter using stub data sources. Outward
actions (apply / email) are intentionally blocked by the AutoBlockGate and
listed at the end as "awaiting approval".

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # then add your OPENROUTER_API_KEY
    python demo.py
"""
from __future__ import annotations

from hookai.config import ConfigError, load_settings
from hookai.gates import AutoBlockGate
from hookai.llm import OpenRouterLLM
from hookai.orchestrator import Orchestrator
from hookai.tools.job_data import make_provider_from_env

SAMPLE_CV = """\
Jordan Rivera
Backend developer, 5 years. jordan@example.com | Lisbon, Portugal

EXPERIENCE
Backend Engineer, Mercado (2021–present)
- Built and scaled Python/FastAPI services handling 2M requests/day on AWS.
- Migrated a monolith to event-driven microservices; cut p95 latency 40%.
Software Engineer, Bytatech (2019–2021)
- PostgreSQL schema design and query optimization; Dockerized the stack.

SKILLS: Python, FastAPI, PostgreSQL, AWS, Docker, REST APIs
EDUCATION: BSc Computer Science, Universidade de Lisboa (2019)
"""

SAMPLE_BIO = (
    "I want a senior backend or platform engineering role, remote-first, ideally EU hours. "
    "Keen to grow into Kubernetes and infra. Looking for €80k+."
)


def main() -> None:
    try:
        settings = load_settings(require_key=True)
    except ConfigError as exc:
        print(exc)
        print("\n(Want to test the wiring without a key? Run:  python smoke_test.py)")
        return

    print(f"Model: {settings.model}")
    provider = make_provider_from_env(verbose=True)
    orch = Orchestrator(OpenRouterLLM(settings), gate=AutoBlockGate(), job_provider=provider)
    print("-" * 60)

    profile = orch.onboard(SAMPLE_CV, SAMPLE_BIO)
    print(f"1. Parsed profile: {profile.identity.name} — {len(profile.skills)} skills")

    jobs, matches = orch.find_matches(profile)
    print(f"2. Discovered {len(jobs)} jobs. Top matches:")
    for m in matches:
        print(f"     {m['score']:>3}  {m['job'].title} @ {m['job'].company} — {m['reason']}")
    if not matches:
        print("   (no matches; stopping)")
        return

    top = matches[0]["job"]
    tailored = orch.tailor_for(profile, top)
    print(f"3. Tailored CV for '{top.title}' — fit {tailored.get('fit_score')}/100; "
          f"missing: {tailored.get('missing_keywords')}")

    _, apply_res = orch.assist_apply(profile, top, tailored.get("ats_cv_markdown", ""))
    print(f"4. Apply -> {apply_res['status']}: {apply_res.get('reason', '')}")

    contact, _, send_res = orch.reach_out(profile, top)
    print(f"5. Contact {contact.email if contact else '—'} -> {send_res['status']}: "
          f"{send_res.get('reason', '')}")

    advice = orch.advise_skills(profile, jobs)
    print(f"6. Skill gaps: {[g.get('skill') for g in advice.get('gaps', [])]}")

    if isinstance(orch.gate, AutoBlockGate) and orch.gate.pending:
        print("-" * 60)
        print(f"{len(orch.gate.pending)} action(s) awaiting YOUR approval (nothing was sent):")
        for action in orch.gate.pending:
            print(f"   • {action.kind} -> {action.target}")


if __name__ == "__main__":
    main()
