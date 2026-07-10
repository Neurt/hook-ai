"""Small deterministic heuristics (no LLM, no I/O) used by the chat backend."""
from __future__ import annotations

import re

_CV_KEYWORDS = ("experience", "education", "skills", "university", "degree", "employment",
                "intern", "engineer", "manager", "analyst", "developer", "gpa", "summary",
                "certification", "graduated")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE_RE = re.compile(r"(\+?\d[\d ()\-]{7,}\d)")


MAX_FULFILL_CV_BYTES = 200 * 1024  # a paid CROO order must fail fast, not hang an LLM


def fulfill_input_error(cv_text: str) -> str | None:
    """Validation for /internal/fulfill inputs. Returns an error message for the
    deliverable body, or None when the input is acceptable."""
    if len((cv_text or "").encode("utf-8", "ignore")) > MAX_FULFILL_CV_BYTES:
        return "cv_text too large (max 200KB of text)"
    return None


def next_search_state(state: dict, what: str, where: str, remote: bool) -> dict:
    """Session search-state: repeating the same query pages forward ("find more
    jobs" gives NEW results); changing the query resets to page 1. `shown` keeps
    ids already displayed so they're never repeated (capped so it can't grow
    unbounded)."""
    key = f"{what.lower().strip()}|{where.lower().strip()}|{int(remote)}"
    same = state.get("key") == key
    return {"key": key,
            "page": int(state.get("page", 1)) + 1 if same else 1,
            "shown": (state.get("shown") or [])[-200:] if same else []}


def looks_like_cv(text: str) -> bool:
    """Is this pasted message a CV rather than a long question?

    Requires length AND at least two independent CV signals (contact details,
    resume-section keywords, many short lines) — so a long question without
    those doesn't get silently 'parsed' as a CV."""
    text = text.strip()
    if len(text) < 120:
        return False
    lowered = text.lower()
    signals = 0
    if _EMAIL_RE.search(text):
        signals += 1
    if _PHONE_RE.search(text):
        signals += 1
    if sum(1 for k in _CV_KEYWORDS if k in lowered) >= 2:
        signals += 1
    if text.count("\n") >= 4:  # CVs paste as many short lines
        signals += 1
    return signals >= 2
