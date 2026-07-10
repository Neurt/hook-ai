"""CV Tailor (Pillar 1) — CV+bio -> canonical Profile, and Profile -> ATS CV.

Pure LLM, no external tools, lowest risk. Good first thing to build for real.
"""
from __future__ import annotations

from ..llm import LLM
from ..profile import Profile
from ..tools.job_data import Job


class CVTailor:
    name = "CV Tailor"
    risk = "low"
    TAG_PARSE = "task:parse_cv"
    TAG_TAILOR = "task:tailor_cv"
    TAG_ATS = "task:to_ats"
    TAG_ASSESS = "task:assess_ats"

    def __init__(self, llm: LLM):
        self.llm = llm

    def parse_cv(self, raw_cv: str, bio: str = "") -> Profile:
        system = (
            f"[{self.TAG_PARSE}] Extract a structured candidate profile from the CV and bio. "
            "Return ONLY JSON of this shape: "
            '{"identity":{"name":"","email":"","phone":"","location":"","links":[]},'
            '"summary":"",'
            '"experience":[{"role":"","org":"","start":"","end":"","bullets":[]}],'
            '"education":[{"degree":"","institution":"","year":""}],'
            '"skills":[{"name":"","level":"","evidence":""}],'
            '"preferences":{"titles":[],"locations":[],"remote":false,"salary_floor":null,'
            '"seniority":"","must_have":[],"avoid":[]}}. '
            "Infer preferences from the bio where stated; otherwise leave them empty. Do not invent facts."
        )
        user = f"CV:\n{raw_cv}\n\nBIO:\n{bio}"
        data = self.llm.complete_json(system, user, max_tokens=3000)
        return Profile.from_dict(data)

    def tailor(self, profile: Profile, job: Job) -> dict:
        system = (
            f"[{self.TAG_TAILOR}] Tailor the candidate's resume to ONE job and make it ATS-friendly. "
            'Return ONLY JSON: {"ats_cv_markdown":"","fit_score":0,"rationale":"","missing_keywords":[]}. '
            "fit_score is 0-100. ATS rules: single column, standard section headings, no tables/images, "
            "mirror the job's key terms truthfully — never claim skills the profile lacks."
        )
        user = (
            f"CANDIDATE PROFILE:\n{profile.to_prompt_text()}\n\n"
            f"TARGET JOB: {job.title} @ {job.company} ({job.location})\n{job.description}"
        )
        return self.llm.complete_json(system, user, max_tokens=2500)

    def assess_ats(self, raw_cv: str) -> dict:
        """Judge whether a CV is already ATS-friendly. Returns {is_ats, score, issues}."""
        system = (
            f"[{self.TAG_ASSESS}] Assess whether this CV is already ATS-friendly: single column, standard "
            "section headings, no tables/columns/text-boxes/images/emojis, consistent dates, plain bullets, "
            "machine-readable contact details. Return ONLY JSON: "
            '{"is_ats":true,"score":0,"issues":[]}. '
            "is_ats=true only if it would parse cleanly with no significant problems; score=0-100 "
            "ATS-readiness; issues=concrete problems (empty list if none)."
        )
        data = self.llm.complete_json(system, f"CV:\n{raw_cv}", max_tokens=600)
        return data if isinstance(data, dict) else {"is_ats": True, "score": 100, "issues": []}

    def to_ats(self, raw_cv: str, target_job: str = "", bio: str = "", instructions: str = "") -> dict:
        """Convert any (possibly non-ATS) CV text into a clean, ATS-friendly version.

        Reformats structure WITHOUT inventing or dropping real content. Optionally
        aligns keywords to a target job. Does not require a parsed Profile (so nothing
        the schema doesn't capture — certs, projects — gets lost)."""
        system = (
            f"[{self.TAG_ATS}] Reformat the candidate's CV into a clean, single-column, ATS-friendly resume. "
            "PRESERVE all real content — every role, bullet, skill, certification, project and date. "
            "Do NOT invent, embellish, or drop information. Rewrite any first-person objective as a concise "
            "professional summary. Use ONE consistent date format like 'Jan 2021 - Present'. "
            "Output MARKDOWN in EXACTLY this structure: "
            "(1) '# Full Name'. "
            "(2) the next line = ALL contact details on ONE line joined by ' | ' (location | phone | email | links). "
            "(3) each section heading as '## SECTION' in UPPERCASE (SUMMARY, EDUCATION, TECHNICAL SKILLS, "
            "PROFESSIONAL EXPERIENCE, PROJECTS, CERTIFICATIONS). "
            "(4) SUMMARY = one plain paragraph. "
            "(5) TECHNICAL SKILLS = one line per group as '**Category:** item, item, item'. "
            "(6) EDUCATION / EXPERIENCE / PROJECTS: each entry is EXACTLY three parts — first a line "
            "'**Left** | Right' (organization/institution/project-name on the LEFT in bold, location or tech-stack "
            "on the RIGHT), then a line '*Sub* | Dates' (degree or role or project-type on the LEFT in italics, "
            "dates on the RIGHT), then '- ' bullet lines (achievements). ALWAYS use ' | ' to separate left from "
            "right; the first line's left MUST be **bold** and the second line MUST be wrapped in *italics*. "
            'Return ONLY JSON: {"ats_cv_markdown":"","changes":[],'
            '"ats_checklist":[{"item":"","ok":true}],"missing_keywords":[]}. '
            "changes = the fixes you made. If a target job is provided, surface its key terms ONLY where the "
            "candidate genuinely has them, and list important terms they truly lack in missing_keywords; "
            "otherwise return missing_keywords as an empty list."
        )
        user = f"RAW CV:\n{raw_cv}"
        if bio:
            user += f"\n\nCANDIDATE NOTES (context, optional):\n{bio}"
        if target_job:
            user += f"\n\nTARGET JOB (for keyword alignment, optional):\n{target_job}"
        if instructions:
            user += f"\n\nUSER ADJUSTMENTS (apply these edits, keep all other content):\n{instructions}"
        return self.llm.complete_json(system, user, max_tokens=4000)
