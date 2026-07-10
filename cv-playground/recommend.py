"""Attach a CV -> get LLM job recommendations + skills to improve.

Workflow:
  1. Read the CV file (.txt/.md/.pdf/.docx).
  2. ATS gate: check if it's ATS-friendly. If not, convert it and let you REVIEW /
     adjust the newly formatted CV before continuing (skip with --no-ats).
  3. Parse the (now ATS-clean) CV into a profile.
  4. If you didn't pass --bio, show INLINE SUGGESTIONS and prompt for your preferences.
  5. Recommend target roles, then skill gaps against those roles.

Usage:
    python recommend.py cvs/your_cv.pdf                 # interactive: ATS gate + bio prompt
    python recommend.py cvs/your_cv.pdf --bio "remote senior data roles"
    python recommend.py cvs/your_cv.pdf --no-ats        # skip the ATS check/convert
    python recommend.py cvs/your_cv.pdf --no-interactive   # no prompts
    python recommend.py cvs/your_cv.docx --json            # raw JSON (non-interactive)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make the sibling app/ package importable, and load its .env (different CWD).
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(APP_DIR, ".env"))
except Exception:
    pass

# Windows consoles default to a non-UTF-8 codepage; keep bullets/dashes readable.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from cv_loader import load_cv_text  # noqa: E402

from hookai.agents import CVTailor, SkillsAdvisor  # noqa: E402
from hookai.config import ConfigError, load_settings  # noqa: E402
from hookai.llm import LLM, OpenRouterLLM  # noqa: E402
from hookai.profile import Profile  # noqa: E402
from hookai.tools import docgen  # noqa: E402
from hookai.tools.job_data import Job  # noqa: E402

RECO_TAG = "task:recommend_roles"
SUGGEST_TAG = "task:suggest_preferences"
_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"  # U+FEFF, sometimes prefixed to piped stdin


def _ask(prompt: str) -> str:
    """input() that tolerates a piped/encoded stdin (stray BOM, EOF)."""
    try:
        return input(prompt).lstrip(_BOM).strip()
    except EOFError:
        return ""


def ensure_ats(cv: CVTailor, cv_text: str, cv_path: str, interactive: bool, json_mode: bool):
    """Check ATS-readiness; if not ATS, convert and (interactively) let the user review.

    Returns (cv_text_to_use, meta)."""
    assessment = cv.assess_ats(cv_text)
    is_ats = bool(assessment.get("is_ats", True))
    score = assessment.get("score")
    issues = assessment.get("issues") or []
    meta = {"is_ats": is_ats, "score": score, "issues": issues, "converted": False, "out_path": None}

    if is_ats:
        if not json_mode:
            print(f"ATS CHECK: ✓ already ATS-friendly (score {score}). Keeping your CV as-is.")
        return cv_text, meta

    if not json_mode:
        print(f"ATS CHECK: ✗ not fully ATS-ready (score {score}).")
        for issue in issues:
            print(f"  • {issue}")
        print("Converting to ATS format…")

    result = cv.to_ats(cv_text)
    ats_md = (result.get("ats_cv_markdown") or "").strip()
    if not ats_md:
        if not json_mode:
            print("(conversion returned nothing — keeping the original CV)")
        return cv_text, meta

    stem = os.path.splitext(os.path.basename(cv_path))[0]
    out_md = os.path.join("out", f"{stem}_ats.md")
    docgen.write_markdown(out_md, ats_md)
    meta.update(converted=True, out_path=out_md, changes=result.get("changes") or [])

    if not interactive:
        if not json_mode:
            print(f"Converted → {out_md} (using it).")
        return ats_md, meta

    # Interactive review: accept / edit-the-file / describe changes to apply.
    while True:
        print(f"\nNEWLY FORMATTED (ATS) CV  (saved → {out_md})\n" + "-" * 64)
        print(ats_md)
        print("-" * 64)
        ans = _ask("  > [Enter = use this CV]  type changes to apply, or 'e' to edit the file: ")
        if not ans:
            return ats_md, meta
        if ans.lower() == "e":
            _ask(f"  Edit {out_md} in your editor, save, then press Enter to reload… ")
            try:
                reloaded = load_cv_text(out_md).strip()
                if reloaded:
                    ats_md = reloaded
            except Exception as exc:
                print(f"  (could not reload: {exc})")
            continue
        print("  Applying your changes…")
        refined = cv.to_ats(ats_md, instructions=ans)
        new_md = (refined.get("ats_cv_markdown") or "").strip()
        if new_md:
            ats_md = new_md
            docgen.write_markdown(out_md, ats_md)


def suggest_preferences(llm: LLM, profile: Profile) -> dict:
    """Inline suggestions: preferences the model infers from the CV, to seed the bio."""
    system = (
        f"[{SUGGEST_TAG}] From the candidate's profile, propose job-search preferences they can "
        "confirm or edit. Return ONLY JSON: "
        '{"suggested_bio":"","target_roles":[],"location":"","remote":true,"seniority":"","salary_hint":""}. '
        "suggested_bio = one natural sentence they could paste as their preferences."
    )
    data = llm.complete_json(system, f"PROFILE:\n{profile.to_prompt_text()}", max_tokens=400)
    return data if isinstance(data, dict) else {}


def prompt_for_bio(llm: LLM, profile: Profile) -> str:
    """Show CV-derived inline suggestions, then ask the user for their preferences."""
    sugg = suggest_preferences(llm, profile)
    print("\nINLINE SUGGESTIONS (inferred from your CV — edit freely):")
    roles = sugg.get("target_roles") or []
    if roles:
        print(f"  • Likely target roles: {', '.join(roles)}")
    if sugg.get("location"):
        print(f"  • Location: {sugg['location']}    Remote: {sugg.get('remote')}")
    if sugg.get("seniority"):
        print(f"  • Seniority: {sugg['seniority']}")
    if sugg.get("salary_hint"):
        print(f"  • Salary: {sugg['salary_hint']}")
    suggested_bio = (sugg.get("suggested_bio") or "").strip()
    if suggested_bio:
        print(f"\n  Suggested bio: {suggested_bio}")

    print("\nDescribe your preferences (target roles, location, remote, salary).")
    entered = _ask("  > [Enter = accept suggestion]  your preferences: ")
    chosen = entered or suggested_bio
    if chosen:
        print(f"  ✓ Using: {chosen}")
    return chosen


def recommend_roles(llm: LLM, profile: Profile, bio: str = "") -> list[dict]:
    system = (
        f"[{RECO_TAG}] Based on the candidate's profile, recommend 3-5 target job roles to pursue. "
        'Return ONLY JSON: {"roles":[{"title":"","why":"","seniority":"","typical_requirements":[]}]}. '
        "Ground each recommendation in the candidate's actual experience and stated preferences; "
        "do not invent skills."
    )
    user = f"PROFILE:\n{profile.to_prompt_text()}\n\nGOALS / BIO:\n{bio or '(none provided)'}"
    data = llm.complete_json(system, user, max_tokens=1500)
    return data.get("roles", []) if isinstance(data, dict) else []


def roles_to_jobs(roles: list[dict]) -> list[Job]:
    """Turn recommended roles into Job objects so SkillsAdvisor can diff against them."""
    jobs: list[Job] = []
    for i, role in enumerate(roles, 1):
        reqs = ", ".join(role.get("typical_requirements", []) or [])
        jobs.append(
            Job(
                id=f"r{i}",
                title=role.get("title", ""),
                company="(target role)",
                location="",
                description=f"{role.get('why', '')} Typical requirements: {reqs}",
            )
        )
    return jobs


def _print_profile(profile: Profile) -> None:
    print("\nPARSED PROFILE")
    print(f"  Name:               {profile.identity.name or '(not found)'}")
    print(f"  Location:           {profile.identity.location or '(not found)'}")
    print(f"  Experience entries: {len(profile.experience)}")
    print(f"  Skills detected:    {', '.join(s.name for s in profile.skills) or '(none)'}")


def _list_available_cvs(requested: str) -> None:
    folder = os.path.dirname(requested) or "."
    try:
        found = [
            f for f in sorted(os.listdir(folder))
            if os.path.splitext(f)[1].lower() in (".txt", ".md", ".pdf", ".docx")
        ]
    except OSError:
        found = []
    if found:
        print(f"\nCVs available in {folder}\\:")
        for name in found:
            print(f"  python recommend.py {os.path.join(folder, name)}")
    else:
        print(
            f"\nNo CV files in {folder}\\ yet. Drop your CV there, then run it by its real "
            "name.\nOr try the bundled sample:\n  python recommend.py cvs\\sample_cv.txt"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM job recommendations + skill gaps from your CV")
    parser.add_argument("cv", help="path to your CV (.txt/.md/.pdf/.docx)")
    parser.add_argument("--bio", default="", help="your goals / preferences (skips the prompt)")
    parser.add_argument("--no-ats", action="store_true", help="skip the ATS check/convert step")
    parser.add_argument("--no-interactive", action="store_true", help="don't prompt (ATS or preferences)")
    parser.add_argument("--json", action="store_true", help="print raw JSON (implies non-interactive)")
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
        _list_available_cvs(args.cv)
        return 1
    if not cv_text.strip():
        print(f"No text extracted from {args.cv}. A scanned/image PDF won't work — export a text-based file.")
        return 1

    llm = OpenRouterLLM(settings)
    cv = CVTailor(llm)
    interactive = not args.no_interactive and not args.json
    if not args.json:
        print(f"Model: {settings.model}\nReading: {args.cv}  ({len(cv_text)} chars)\n" + "=" * 64)

    # 1. ATS gate — check, and convert + review if needed.
    ats_meta: dict = {}
    if not args.no_ats:
        cv_text, ats_meta = ensure_ats(cv, cv_text, args.cv, interactive=interactive, json_mode=args.json)

    # 2. Parse the (now ATS-clean) CV.
    profile = cv.parse_cv(cv_text, args.bio)
    if not args.json:
        _print_profile(profile)

    # 3. Get the bio: --bio wins; otherwise prompt interactively with inline suggestions.
    bio = args.bio
    if not bio and interactive:
        bio = prompt_for_bio(llm, profile)
    bio = bio or ""

    # 4. Recommend roles, then skill gaps against those roles.
    roles = recommend_roles(llm, profile, bio)
    target_jobs = roles_to_jobs(roles)
    advice = SkillsAdvisor(llm).analyze(profile, target_jobs) if target_jobs else {"gaps": [], "plan": []}

    if args.json:
        print(json.dumps(
            {"ats": ats_meta, "profile": profile.to_dict(), "bio": bio, "roles": roles, "skills": advice},
            indent=2,
        ))
        return 0

    print("\nRECOMMENDED ROLES")
    if not roles:
        print("  (model returned none)")
    for role in roles:
        print(f"  • {role.get('title', '?')}  [{role.get('seniority', '')}]")
        if role.get("why"):
            print(f"      why: {role['why']}")

    print("\nSKILLS TO IMPROVE")
    gaps = advice.get("gaps", []) if isinstance(advice, dict) else []
    if not gaps:
        print("  (none identified)")
    for gap in gaps:
        print(f"  • [{gap.get('priority', '')}] {gap.get('skill', '')} — {gap.get('why', '')}")
    plan = advice.get("plan", []) if isinstance(advice, dict) else []
    if plan:
        print("\n  Suggested actions:")
        for step in plan:
            print(f"    - {step.get('skill', '')}: {step.get('action', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
