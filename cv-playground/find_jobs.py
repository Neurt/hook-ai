"""Attach a CV -> search REAL jobs (Adzuna) and rank them against the CV.

Needs free Adzuna credentials: https://developer.adzuna.com/
Set ADZUNA_APP_ID / ADZUNA_APP_KEY (and optionally ADZUNA_COUNTRY) in ../app/.env.
Without them it falls back to stub sample jobs so you can still see the flow.

Usage:
    python find_jobs.py cvs/sample_cv.txt --where London
    python find_jobs.py cvs/my_cv.pdf --what "data engineer" --where Remote --remote
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

from hookai.agents import CVTailor, MatchRank  # noqa: E402
from hookai.config import ConfigError, load_settings  # noqa: E402
from hookai.llm import OpenRouterLLM  # noqa: E402
from hookai.profile import Preferences  # noqa: E402
from hookai.tools.job_data import AdzunaError, make_provider_from_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Search real jobs (Adzuna) and rank them against a CV")
    parser.add_argument("cv", help="path to your CV (.txt/.md/.pdf/.docx)")
    parser.add_argument("--what", default="", help="search keywords (default: derived from the CV)")
    parser.add_argument("--where", default="", help="location — must match ADZUNA_COUNTRY")
    parser.add_argument("--remote", action="store_true", help="bias the search toward remote roles")
    parser.add_argument("--limit", type=int, default=10, help="jobs to fetch (default 10)")
    parser.add_argument("--top", type=int, default=5, help="top matches to show (default 5)")
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
    profile = CVTailor(llm).parse_cv(cv_text, "")

    # Build the search from CLI args, falling back to what we parsed from the CV.
    what = args.what or " ".join(profile.preferences.titles) or (
        profile.experience[0].role if profile.experience else ""
    )
    where = args.where or (
        profile.preferences.locations[0] if profile.preferences.locations else profile.identity.location
    )
    prefs = Preferences(
        titles=[what] if what else [],
        locations=[where] if where else [],
        remote=args.remote or profile.preferences.remote,
        salary_floor=profile.preferences.salary_floor,
    )
    print(f"CV: {profile.identity.name or args.cv}")
    print(f"Searching jobs — what={what!r}, where={where!r}, remote={prefs.remote}")

    provider = make_provider_from_env(verbose=True)
    try:
        jobs = provider.search(prefs, limit=args.limit)
    except AdzunaError as exc:
        print(f"\nJob search failed: {exc}")
        print("Set ADZUNA_APP_ID / ADZUNA_APP_KEY in ..\\app\\.env "
              "(free key at https://developer.adzuna.com/).")
        return 1

    if not jobs:
        print("\nNo jobs returned. Try different --what / --where, or set ADZUNA_COUNTRY to match the location.")
        return 0

    print(f"Fetched {len(jobs)} jobs; ranking against the CV...\n" + "=" * 64)
    ranked = MatchRank(llm).rank(profile, jobs, top_k=args.top)
    if not ranked:
        print("(model returned no ranking)")
        return 0

    for match in ranked:
        job = match["job"]
        bits = [job.location or "—"]
        if job.salary:
            bits.append(job.salary)
        if job.remote:
            bits.append("remote")
        print(f"[{match['score']:>3}] {job.title} @ {job.company}")
        print(f"      {'  ·  '.join(bits)}")
        if match.get("reason"):
            print(f"      why: {match['reason']}")
        if job.url:
            print(f"      {job.url}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
