"""Assisted-apply form filling (Pillar 2b, last mile) — pure logic.

Maps a candidate profile + prepared application package to LABEL-PATTERN fill
actions. Label matching (Playwright get_by_label) survives ATS DOM changes far
better than hardcoded CSS selectors. The browser driving lives in
cv-playground/assist_apply.py; this module stays import-safe without playwright.

COMPLIANCE: the plan never includes a submit action. The human reviews the
filled form in a visible browser and clicks submit themselves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..profile import Profile


def detect_ats(url: str) -> str:
    """Which ATS hosts this posting — for messaging/telemetry, not selectors."""
    host = (url or "").lower()
    if re.search(r"(job-)?boards?\.greenhouse\.io|greenhouse\.io/", host):
        return "greenhouse"
    if "jobs.lever.co" in host:
        return "lever"
    if "jobs.ashbyhq.com" in host:
        return "ashby"
    return "generic"


def split_name(full: str) -> tuple[str, str]:
    """"Jane van der Berg" -> ("Jane", "van der Berg"). Single word -> (word, "")."""
    parts = (full or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


@dataclass
class FillAction:
    label_pattern: str  # case-insensitive regex matched against field labels
    value: str
    kind: str = "text"  # text | file


def build_fill_plan(profile: Profile, package: dict, resume_path: str = "") -> list[FillAction]:
    """Fill actions for the standard application fields. Empty values produce no
    action (never blank out a field). Screening questions are NOT auto-filled —
    they're printed for the human to answer; auto-answering unknown questions
    risks wrong legal/eligibility claims."""
    first, last = split_name(profile.identity.name)
    fields = package.get("fields", {}) if isinstance(package, dict) else {}
    candidates = [
        FillAction("first.?name", first),
        FillAction("last.?name|family.?name|surname", last),
        FillAction("full.?name|^name$", (profile.identity.name or "").strip()),
        FillAction("e-?mail", profile.identity.email or ""),
        FillAction("phone|mobile", profile.identity.phone or ""),
        FillAction("linkedin", str(fields.get("linkedin", "") or "")),
        FillAction("cover.?letter|message|why.*(join|interest|apply)",
                   str(package.get("cover_note", "") or "") if isinstance(package, dict) else ""),
        FillAction("resume|\\bcv\\b|curriculum", resume_path, kind="file"),
    ]
    return [a for a in candidates if a.value]
