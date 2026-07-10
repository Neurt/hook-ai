"""Skills Advisor (Pillar 4) — diff the profile against target roles -> skill gaps.

Pure analysis on data already gathered; no outward action.
"""
from __future__ import annotations

from ..llm import LLM
from ..profile import Profile
from ..tools.job_data import Job


class SkillsAdvisor:
    name = "Skills Advisor"
    risk = "low"
    TAG = "task:skill_gaps"

    def __init__(self, llm: LLM):
        self.llm = llm

    def analyze(self, profile: Profile, jobs: list[Job]) -> dict:
        system = (
            f"[{self.TAG}] Compare the candidate's skills to the target roles and find gaps. "
            'Return ONLY JSON: {"gaps":[{"skill":"","why":"","priority":"high|med|low"}],'
            '"plan":[{"skill":"","action":""}]}. '
            "Prioritise by how often a missing skill appears across the roles."
        )
        jds = "\n\n".join(f"{j.title} @ {j.company}:\n{j.description[:400]}" for j in jobs)
        user = f"CANDIDATE:\n{profile.to_prompt_text()}\n\nTARGET ROLES:\n{jds}"
        return self.llm.complete_json(system, user)
