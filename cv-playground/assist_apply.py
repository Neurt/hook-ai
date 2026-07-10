"""Assisted apply (Pillar 2b, last mile) — the agent fills the form, YOU submit.

Opens the job's application page in a VISIBLE browser, fills the standard fields
(name, email, phone, cover note, resume upload) from your CV, prints the prepared
screening answers for you to copy in, then STOPS. You review everything and click
submit yourself — this tool never submits, by design (see docs/feasibility.md).

Works best on ATS-hosted forms (Greenhouse, Lever, Ashby); label-based filling
degrades gracefully elsewhere (unfilled fields are listed for manual entry).

Setup (one-time, on your machine — not in Docker):
    pip install playwright
    playwright install chromium

Usage:
    python assist_apply.py cvs/my_cv.pdf --url "https://boards.greenhouse.io/acme/jobs/401"
    python assist_apply.py cvs/my_cv.pdf --url <apply-url> --title "Backend Dev" --company Acme
"""
from __future__ import annotations

import argparse
import os
import re
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

from hookai.agents import ApplicationAssistant, CVTailor  # noqa: E402
from hookai.config import ConfigError, load_settings  # noqa: E402
from hookai.llm import OpenRouterLLM  # noqa: E402
from hookai.tools.formfill import build_fill_plan, detect_ats  # noqa: E402
from hookai.tools.job_data import Job  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill a job application form; you submit.")
    ap.add_argument("cv", help="CV file (txt/md/pdf/docx)")
    ap.add_argument("--url", required=True, help="application page URL")
    ap.add_argument("--title", default="", help="job title (helps the cover note)")
    ap.add_argument("--company", default="", help="company name")
    ap.add_argument("--resume", default="", help="resume file to upload (defaults to the CV if PDF)")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed. Run:\n  pip install playwright\n  playwright install chromium")
        return 1

    try:
        settings = load_settings(require_key=True)
    except ConfigError as e:
        print(f"config error: {e}")
        return 1
    llm = OpenRouterLLM(settings)

    print(f"[1/4] Reading CV: {args.cv}")
    cv_text = load_cv_text(args.cv)
    profile = CVTailor(llm).parse_cv(cv_text)

    job = Job(id="cli", title=args.title or "the role", company=args.company or "",
              location="", description="", url=args.url)
    print("[2/4] Preparing application package (fields, screening answers, cover note)…")
    package = ApplicationAssistant(llm).prepare(profile, job, "")

    resume = args.resume or (args.cv if args.cv.lower().endswith(".pdf") else "")
    plan = build_fill_plan(profile, package, resume_path=os.path.abspath(resume) if resume else "")
    ats = detect_ats(args.url)
    print(f"[3/4] Opening {ats} application form — a browser window will appear.")

    filled, missed = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # let ATS scripts render the form

        # Dead-posting guard: Greenhouse redirects closed jobs to the company
        # board with ?error=true; a page with no inputs has no form to fill.
        if "error=true" in page.url or page.locator("input, textarea").count() == 0:
            print(f"\nNo application form here — the posting is likely CLOSED.")
            print(f"Landed at: {page.url}")
            print("Grab a live posting URL (ask Hook AI chat to find jobs) and retry.")
            input("Press Enter to close the browser… ")
            browser.close()
            return 1

        for action in plan:
            try:
                field = page.get_by_label(re.compile(action.label_pattern, re.I)).first
                if action.kind == "file":
                    field.set_input_files(action.value, timeout=4000)
                else:
                    field.fill(action.value, timeout=4000)
                filled.append(action.label_pattern.split("|")[0])
            except Exception:
                # File inputs are often visually hidden with a generic "Attach"
                # label (Greenhouse) — fall back to the first file input.
                if action.kind == "file":
                    try:
                        page.locator('input[type="file"]').first.set_input_files(
                            action.value, timeout=4000)
                        filled.append("resume (via file input)")
                        continue
                    except Exception:
                        pass
                missed.append(action.label_pattern.split("|")[0])

        print(f"\nFilled: {', '.join(filled) or 'nothing (form may use unusual labels)'}")
        if missed:
            print(f"Fill these manually: {', '.join(missed)}")
        cover = str(package.get("cover_note", "") or "") if isinstance(package, dict) else ""
        if cover and any(m.startswith("cover") for m in missed):
            print(f"\nCover note (paste it into the cover letter field):\n{cover}")
        answers = package.get("screening_answers", []) if isinstance(package, dict) else []
        if answers:
            print("\nPrepared screening answers (copy the ones the form asks for):")
            for a in answers:
                if isinstance(a, dict):
                    print(f"  Q: {a.get('question', '')}\n  A: {a.get('answer', '')}")
        print("\n[4/4] REVIEW the form in the browser. Fix anything off.")
        print("      Submitting is YOUR click — this tool never submits.")
        input("      Press Enter here when you're done to close the browser… ")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
