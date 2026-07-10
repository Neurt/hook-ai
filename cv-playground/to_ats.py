"""Convert a non-ATS CV into a clean, ATS-friendly version.

Reads any CV (.txt/.md/.pdf/.docx), has the model reformat it for ATS parsing while
PRESERVING all real content (no inventing, no dropping), prints the result, and
saves it to out/. Optionally aligns to a job's keywords and/or writes a .docx.

Usage:
    python to_ats.py cvs/messy_cv.txt
    python to_ats.py cvs/my_cv.pdf --job cvs/jd.txt      # align keywords to a job
    python to_ats.py cvs/my_cv.pdf --docx                # also write an uploadable .docx
    python to_ats.py cvs/my_cv.pdf -o out/clean.md
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

from hookai.agents import CVTailor  # noqa: E402
from hookai.config import ConfigError, load_settings  # noqa: E402
from hookai.llm import OpenRouterLLM  # noqa: E402
from hookai.tools import docgen  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a non-ATS CV into ATS format")
    parser.add_argument("cv", help="path to your CV (.txt/.md/.pdf/.docx)")
    parser.add_argument("--job", default="", help="optional path to a job description to align keywords to")
    parser.add_argument("-o", "--out", default="", help="output .md path (default: out/<cv>_ats.md)")
    parser.add_argument("--docx", action="store_true", help="also write an uploadable .docx")
    parser.add_argument("--pdf", action="store_true", help="also write an uploadable .pdf")
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
    if not cv_text.strip():
        print(f"No text extracted from {args.cv}. A scanned/image PDF won't work — export a text-based file.")
        return 1

    job_text = ""
    if args.job:
        try:
            job_text = load_cv_text(args.job)
        except (FileNotFoundError, ValueError) as exc:
            print(f"(ignoring --job: {exc})")

    llm = OpenRouterLLM(settings)
    print(f"Model: {settings.model}\nReading: {args.cv}  ({len(cv_text)} chars)\n" + "=" * 64)

    result = CVTailor(llm).to_ats(cv_text, target_job=job_text)
    ats_md = (result.get("ats_cv_markdown") or "").strip()
    if not ats_md:
        print("The model did not return a reformatted CV. Try re-running.")
        return 1

    stem = os.path.splitext(os.path.basename(args.cv))[0]
    out_md = args.out or os.path.join("out", f"{stem}_ats.md")
    docgen.write_markdown(out_md, ats_md)

    print(f"ATS CV  (saved -> {out_md})\n" + "-" * 64)
    print(ats_md)
    print("-" * 64)

    changes = result.get("changes") or []
    if changes:
        print("WHAT CHANGED")
        for change in changes:
            print(f"  • {change}")
    checklist = result.get("ats_checklist") or []
    if checklist:
        print("\nATS CHECKLIST")
        for item in checklist:
            mark = "✓" if item.get("ok") else "✗"
            print(f"  {mark} {item.get('item', '')}")
    missing = result.get("missing_keywords") or []
    if missing and job_text:
        print("\nMISSING KEYWORDS (genuinely absent vs. the target job):")
        print("  " + ", ".join(missing))

    if args.docx:
        out_docx = os.path.splitext(out_md)[0] + ".docx"
        docgen.write_docx_from_markdown(out_docx, ats_md)
        print(f"\nUploadable DOCX saved -> {out_docx}")
    if args.pdf:
        out_pdf = os.path.splitext(out_md)[0] + ".pdf"
        docgen.write_pdf_from_markdown(out_pdf, ats_md)
        print(f"Uploadable PDF saved -> {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
