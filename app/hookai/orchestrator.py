"""Orchestrator — wires the specialists, owns the approval gate, exposes the flow.

Tools default to the stub connectors so the whole thing runs with only an
OpenRouter key. Swap any of them for a real provider via the constructor.
"""
from __future__ import annotations

from typing import Optional

from .agents import (
    ApplicationAssistant,
    CVTailor,
    JobScout,
    MatchRank,
    Outreach,
    SkillsAdvisor,
)
from .gates import ApprovalGate, AutoBlockGate
from .llm import LLM
from .profile import Preferences, Profile
from .tools.email import EmailSender, StubEmailSender
from .tools.enrichment import EnrichmentProvider, StubEnrichmentProvider
from .tools.job_data import Job, JobDataProvider, StubJobDataProvider


def _sort_matches(matches: list[dict]) -> list[dict]:
    """Score is the primary rank; among equal scores the freshest posting wins
    (ISO date strings compare lexicographically; undated jobs sort last)."""
    return sorted(matches, key=lambda m: (m.get("score", 0), m["job"].posted or ""),
                  reverse=True)


def _with_country(where: str, profile_location: str) -> str:
    """Qualify a bare home city with the profile's country ("Jakarta" +
    "Jakarta, Indonesia" gives "Jakarta, Indonesia") — Jooble needs the country.
    Foreign cities are left alone: appending the home country would poison them."""
    where = (where or "").strip()
    profile_location = (profile_location or "").strip()
    if not where or "," in where or "," not in profile_location:
        return where
    if where.lower() in profile_location.lower():
        return f"{where}, {profile_location.rsplit(',', 1)[-1].strip()}"
    return where


class Orchestrator:
    def __init__(
        self,
        llm: LLM,
        gate: Optional[ApprovalGate] = None,
        job_provider: Optional[JobDataProvider] = None,
        enrichment: Optional[EnrichmentProvider] = None,
        email_sender: Optional[EmailSender] = None,
    ):
        self.llm = llm
        self.gate: ApprovalGate = gate or AutoBlockGate()
        self.cv = CVTailor(llm)
        self.scout = JobScout(job_provider or StubJobDataProvider())
        self.matcher = MatchRank(llm)
        self.applier = ApplicationAssistant(llm)
        self.outreach = Outreach(
            llm, enrichment or StubEnrichmentProvider(), email_sender or StubEmailSender()
        )
        self.skills = SkillsAdvisor(llm)

    # ── Pillar entry points ────────────────────────────────────────────
    def onboard(self, raw_cv: str, bio: str = "") -> Profile:
        return self.cv.parse_cv(raw_cv, bio)

    def find_matches(self, profile: Profile, top_k: int = 5) -> tuple[list[Job], list[dict]]:
        jobs = self.scout.discover(profile.preferences)
        return jobs, self.matcher.rank(profile, jobs, top_k=top_k)

    def tailor_for(self, profile: Profile, job: Job) -> dict:
        return self.cv.tailor(profile, job)

    def assist_apply(self, profile: Profile, job: Job, ats_cv: str = "") -> tuple[dict, dict]:
        draft = self.applier.prepare(profile, job, ats_cv)
        return draft, self.applier.submit(draft, job, self.gate)

    def reach_out(self, profile: Profile, job: Job):
        contact = self.outreach.find_contact(job)
        message = self.outreach.draft(profile, job, contact)
        return contact, message, self.outreach.send(message, contact, self.gate)

    def advise_skills(self, profile: Profile, jobs: list[Job]) -> dict:
        return self.skills.analyze(profile, jobs)

    # ── Capability API (used by the chat backend + CROO fulfilment) ─────
    def to_ats(self, cv_text: str, target_job: str = "") -> dict:
        """Pillar 1: reformat a CV into ATS markdown (+ changes/checklist)."""
        return self.cv.to_ats(cv_text, target_job=target_job)

    def recommend(self, profile: Profile) -> dict:
        """Pillar 4: target-role recommendations + a skill-gap analysis against them."""
        roles = self._recommend_roles(profile)
        targets = [
            Job(id=f"r{i}", title=r.get("title", ""), company="(target role)",
                location="", description=r.get("why", ""))
            for i, r in enumerate(roles, 1)
        ]
        skills = self.skills.analyze(profile, targets) if targets else {"gaps": [], "plan": []}
        return {"roles": roles, "skills": skills}

    def _recommend_roles(self, profile: Profile) -> list[dict]:
        system = (
            "[task:recommend_roles] Recommend 3-5 target roles from the profile. Return ONLY JSON: "
            '{"roles":[{"title":"","why":"","seniority":""}]}. Ground them in the actual experience.'
        )
        data = self.llm.complete_json(system, f"PROFILE:\n{profile.to_prompt_text()}")
        return data.get("roles", []) if isinstance(data, dict) else []

    def find_jobs(self, profile: Profile, what: str = "", where: str = "",
                  remote: bool = False, limit: int = 8, page: int = 1,
                  exclude_ids: set[str] | None = None) -> list[dict]:
        """Pillar 2a: discover live jobs and rank them against the profile (serialized).
        `page` + `exclude_ids` power "find more jobs": next result page, minus
        anything the user was already shown."""
        what = what or " ".join(profile.preferences.titles) or (
            profile.experience[0].role if profile.experience else "")
        where = where or (profile.preferences.locations[0] if profile.preferences.locations
                          else profile.identity.location)
        # Jooble needs a country in the location ("Jakarta" finds 0, "Jakarta,
        # Indonesia" finds 13) — qualify a bare home city from the profile.
        where = _with_country(where, profile.identity.location)
        prefs = Preferences(titles=[what] if what else [],
                            locations=[where] if where else [], remote=remote,
                            page=max(1, page))
        jobs = self.scout.discover(prefs, limit=limit)
        if exclude_ids:
            jobs = [j for j in jobs if j.id not in exclude_ids]
        if not jobs:
            return []
        ranked = self.matcher.rank(profile, jobs, top_k=limit)
        # Don't surface listings the ranker says don't fit this candidate (score 0-100).
        ranked = _sort_matches([m for m in ranked if m["score"] >= 25])
        return [{"id": m["job"].id, "title": m["job"].title, "company": m["job"].company,
                 "location": m["job"].location, "salary": m["job"].salary, "url": m["job"].url,
                 "source": m["job"].source, "score": m["score"], "reason": m["reason"],
                 "posted": m["job"].posted}
                for m in ranked]

    def recommend_certifications(self, profile: Profile) -> dict:
        """Pillar 4 extension: REAL, industry-recognized certifications/licenses for the
        candidate's target field. Constrained to well-established credentials from real
        issuing bodies — the model must omit anything it isn't sure exists."""
        system = (
            "[task:recommend_certs] Recommend certifications/licenses for the candidate's target "
            "industry and experience level. STRICT RULES: only REAL, widely recognized credentials "
            "from established issuing bodies (professional societies, vendors, regulators) that you "
            "are certain exist — e.g. the class of SOA/CAS actuarial exams, CFA, FRM, AWS/Azure/GCP "
            "certifications, PMP, CPA, Google/Microsoft data certs. NEVER invent or approximate a "
            "certification name; if unsure it exists, OMIT it. Prefer credentials relevant to the "
            "candidate's region when stated. Return ONLY JSON: "
            '{"certifications":[{"name":"","issuer":"","official_site":"",'
            '"level":"foundational|intermediate|advanced","why":"","typical_path":""}]}. '
            'official_site = the issuer\'s official domain only (e.g. "soa.org") — never a deep link. '
            "5-8 items ordered by what this candidate should pursue FIRST given their level. "
            "typical_path = where it sits in the standard progression (e.g. 'usually the first SOA exam')."
        )
        data = self.llm.complete_json(system, f"PROFILE:\n{profile.to_prompt_text()}", max_tokens=2000)
        certs = data.get("certifications", []) if isinstance(data, dict) else []
        return {"certifications": certs}

    def prepare_application(self, profile: Profile, job: Job) -> dict:
        """Pillar 2b, ASSISTED apply: pre-fill the application; the human submits.
        The submit is routed through the approval gate, which blocks by design —
        we return the prepared package + apply link, never click submit ourselves."""
        draft, submit = self.assist_apply(profile, job)
        return {
            "job": {"id": job.id, "title": job.title, "company": job.company,
                    "location": job.location, "url": job.url},
            "fields": draft.get("fields", {}) if isinstance(draft, dict) else {},
            "screening_answers": draft.get("screening_answers", []) if isinstance(draft, dict) else [],
            "cover_note": draft.get("cover_note", "") if isinstance(draft, dict) else "",
            "submit_status": submit.get("status", ""),
        }

    def draft_email(self, profile: Profile, job: Job) -> dict:
        """Pillar 3: draft a personal outreach email (draft only — no autonomous send)."""
        try:
            contact = self.outreach.find_contact(job)
        except Exception:
            contact = None
        if contact is None:
            contact = StubEnrichmentProvider().find_hiring_contact(job.company, job.title)
        message = self.outreach.draft(profile, job, contact, signoff="personal")
        return {"to": contact.email if contact else "", "source": getattr(contact, "source", ""),
                "subject": message.get("subject", ""), "body": message.get("body", "")}
