"""Match & Rank — score job<->profile fit and order the shortlist.

One LLM call scores all candidate jobs; results are joined back to Job objects.
"""
from __future__ import annotations

from ..llm import LLM
from ..profile import Profile
from ..tools.job_data import Job


class MatchRank:
    name = "Match & Rank"
    risk = "low"
    TAG = "task:rank_jobs"

    def __init__(self, llm: LLM):
        self.llm = llm

    def rank(self, profile: Profile, jobs: list[Job], top_k: int = 5) -> list[dict]:
        if not jobs:
            return []
        system = (
            f"[{self.TAG}] Score how well the candidate fits each job (0-100). "
            'Return ONLY JSON: {"matches":[{"id":"","score":0,"reason":""}]}. '
            "Use the exact job ids given. Be honest — a poor fit should score low."
        )
        joblist = "\n".join(
            f"[{j.id}] {j.title} @ {j.company} ({j.location}) :: {j.description[:300]}" for j in jobs
        )
        user = f"CANDIDATE:\n{profile.to_prompt_text()}\n\nJOBS:\n{joblist}"
        data = self.llm.complete_json(system, user)
        raw = data.get("matches", []) if isinstance(data, dict) else []
        by_id = {j.id: j for j in jobs}
        enriched = [
            {"job": by_id[m["id"]], "score": int(m.get("score", 0)), "reason": m.get("reason", "")}
            for m in raw
            if isinstance(m, dict) and m.get("id") in by_id
        ]
        enriched.sort(key=lambda x: x["score"], reverse=True)
        return enriched[:top_k]
