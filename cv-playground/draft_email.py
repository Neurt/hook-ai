"""Draft a personalized outreach email to a job's hiring contact, from your CV.

Pick a real job (live from Adzuna, or specify one manually) and the model drafts a
tailored 1:1 email signed with your details. The hiring contact is a STUB placeholder
for now — swap in Hunter.io/Apollo to find a real public work email. Nothing is sent.

Usage:
    python draft_email.py cvs/sample_cv.txt --what "data analyst" --where London
    python draft_email.py cvs/my_cv.pdf --what "data engineer" --where London --pick 2
    python draft_email.py cvs/my_cv.pdf --company "Acme Ltd" --title "Data Analyst"
"""
from __future__ import annotations

import argparse
import os
import sys

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(APP_DIR, ".env"))
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from cv_loader import load_cv_text  # noqa: E402

from hookai.agents import CVTailor, Outreach  # noqa: E402
from hookai.config import ConfigError, load_settings  # noqa: E402
from hookai.llm import OpenRouterLLM  # noqa: E402
from hookai.profile import Preferences  # noqa: E402
from hookai.tools import docgen  # noqa: E402
from hookai.tools.email import StubEmailSender  # noqa: E402
from hookai.tools.enrichment import (  # noqa: E402
    HunterError,
    StubEnrichmentProvider,
    make_enrichment_from_env,
)
from hookai.tools.job_data import AdzunaError, Job, make_provider_from_env  # noqa: E402

_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"


def _ask(prompt: str) -> str:
    try:
        return input(prompt).lstrip(_BOM).strip()
    except EOFError:
        return ""


def choose_job(args, profile, interactive: bool):
    if args.company and args.title:
        return Job(
            id="manual", title=args.title, company=args.company,
            location=args.where or "", description=args.desc or "", url="", source="manual",
        )
    what = args.what or " ".join(profile.preferences.titles) or (
        profile.experience[0].role if profile.experience else ""
    )
    where = args.where or (
        profile.preferences.locations[0] if profile.preferences.locations else profile.identity.location
    )
    prefs = Preferences(titles=[what] if what else [], locations=[where] if where else [], remote=args.remote)
    provider = make_provider_from_env(verbose=True)
    try:
        jobs = provider.search(prefs, limit=args.limit)
    except AdzunaError as exc:
        print(f"Job search failed: {exc}")
        return None
    if not jobs:
        print("No jobs found. Try --what/--where, or specify --company and --title.")
        return None

    print(f"\nJobs for what={what!r}, where={where!r}:")
    for i, job in enumerate(jobs, 1):
        extra = f"  ·  {job.salary}" if job.salary else ""
        print(f"  [{i}] {job.title} @ {job.company} — {job.location}{extra}")
    idx = args.pick
    if idx is None and interactive:
        ans = _ask(f"\nPick a job to draft an email for [1-{len(jobs)}, default 1]: ")
        idx = int(ans) if ans.isdigit() else 1
    idx = max(1, min(idx or 1, len(jobs)))
    return jobs[idx - 1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft an outreach email to a job's hiring contact")
    parser.add_argument("cv", help="path to your CV (.txt/.md/.pdf/.docx)")
    parser.add_argument("--what", default="", help="job keywords (default: from CV)")
    parser.add_argument("--where", default="", help="location (must match ADZUNA_COUNTRY)")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--limit", type=int, default=8, help="jobs to list (default 8)")
    parser.add_argument("--pick", type=int, default=None, help="job number to use (default: ask, or 1)")
    parser.add_argument("--company", default="", help="manual job: company (with --title, skips search)")
    parser.add_argument("--title", default="", help="manual job: role title")
    parser.add_argument("--desc", default="", help="manual job: optional description")
    parser.add_argument("--no-interactive", action="store_true", help="don't prompt; use --pick or top job")
    args = parser.parse_args()

    try:
        settings = load_settings(require_key=True)
    except ConfigError as exc:
        print(exc)
        return 1
    try:
        cv_text = load_cv_text(args.cv)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    llm = OpenRouterLLM(settings)
    interactive = not args.no_interactive
    print(f"Model: {settings.model}\nReading: {args.cv}\n" + "=" * 64)
    profile = CVTailor(llm).parse_cv(cv_text, "")

    job = choose_job(args, profile, interactive)
    if job is None:
        return 1

    enrichment = make_enrichment_from_env(verbose=True)
    outreach = Outreach(llm, enrichment, StubEmailSender())
    try:
        contact = outreach.find_contact(job)
    except HunterError as exc:
        print(f"(contact lookup failed: {exc})")
        contact = None
    if contact is None:
        print("(no real contact found — using a placeholder address)")
        contact = StubEnrichmentProvider().find_hiring_contact(job.company, job.title)
    message = outreach.draft(profile, job, contact, signoff="personal")
    subject = message.get("subject", "")
    body = message.get("body", "")

    print("\n" + "=" * 64)
    print(f"DRAFT EMAIL  —  {job.title} @ {job.company}")
    print("=" * 64)
    to_addr = contact.email if contact else "(no contact)"
    if contact and contact.source == "hunter":
        note = f"   [Hunter.io: {contact.name}, {contact.title}]"
    else:
        note = "   [placeholder — set HUNTER_API_KEY for a real address]"
    print(f"To:      {to_addr}{note}")
    if contact and contact.source == "hunter" and contact.public_source_url:
        print(f"         source: {contact.public_source_url}")
    print(f"Subject: {subject}\n")
    print(body)
    print("=" * 64)

    safe = "".join(c if c.isalnum() else "_" for c in job.company)[:40] or "job"
    out_path = os.path.join("out", f"email_{safe}.md")
    docgen.write_markdown(out_path, f"To: {to_addr}\nSubject: {subject}\n\n{body}\n")
    print(f"\nSaved draft -> {out_path}")
    print("\nNOTE: this is a DRAFT — nothing was sent.")
    if not (contact and contact.source == "hunter"):
        print("  • Contact is a placeholder. Set HUNTER_API_KEY (free at hunter.io/api-keys)")
        print("    for a real public hiring email.")
    print("  • To actually send, wire a provider (SES/SendGrid/SMTP) into")
    print("    app/hookai/tools/email.py — review CAN-SPAM/GDPR before any automation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
