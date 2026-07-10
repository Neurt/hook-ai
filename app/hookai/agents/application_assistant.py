"""Application Assistant (Pillar 2b) — HIGH RISK, gated.

Pre-fills an application and drafts answers, then routes the *submit* through an
approval gate. The default gate blocks it: the agent prepares, the human submits.
This is the "assisted apply, not autonomous bot" stance from docs/feasibility.md.
"""
from __future__ import annotations

from ..gates import ApprovalGate, OutwardAction
from ..llm import LLM
from ..profile import Profile
from ..tools.job_data import Job


class ApplicationAssistant:
    name = "Application Assistant"
    risk = "high"
    TAG = "task:prepare_application"

    def __init__(self, llm: LLM):
        self.llm = llm

    def prepare(self, profile: Profile, job: Job, ats_cv_markdown: str = "") -> dict:
        system = (
            f"[{self.TAG}] Pre-fill a job application from the profile. "
            'Return ONLY JSON: {"fields":{"full_name":"","email":"","phone":"","location":""},'
            '"screening_answers":[{"question":"","answer":""}],"cover_note":""}. '
            "Be truthful to the profile; never invent credentials or experience."
        )
        user = (
            f"PROFILE:\n{profile.to_prompt_text()}\n\n"
            f"JOB: {job.title} @ {job.company}\n{job.description}"
        )
        draft = self.llm.complete_json(system, user)
        if isinstance(draft, dict):
            draft["_job_id"] = job.id
        return draft

    def submit(self, draft: dict, job: Job, gate: ApprovalGate) -> dict:
        action = OutwardAction(
            kind="submit_application",
            target=f"{job.company} ({job.url or job.source})",
            summary=f"Submit application to '{job.title}' @ {job.company}",
            payload=draft,
        )
        decision = gate.review(action)
        if not decision.approved:
            return {"status": "pending_approval", "reason": decision.reason, "action": action}
        # A real connector would fill + submit the form here (still ToS-permitting).
        return {"status": "submitted_stub", "job_id": job.id}
