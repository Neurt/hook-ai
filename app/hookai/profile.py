"""The canonical Profile — the single structured object every pillar reads/writes.

Parsed once from the CV+bio, then reused for matching, tailoring, applying and
skill-gap analysis. Under the CROO model this is the data that stays sovereign.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Identity:
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = field(default_factory=list)


@dataclass
class Experience:
    role: str = ""
    org: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class Education:
    degree: str = ""
    institution: str = ""
    year: str = ""


@dataclass
class Skill:
    name: str = ""
    level: str = ""  # e.g. beginner / intermediate / advanced
    evidence: str = ""


@dataclass
class Preferences:
    titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote: bool = False
    salary_floor: int | None = None
    seniority: str = ""
    must_have: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    page: int = 1  # result page for "find more jobs" (providers that paginate use it)


@dataclass
class Profile:
    identity: Identity = field(default_factory=Identity)
    summary: str = ""
    experience: list[Experience] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    preferences: Preferences = field(default_factory=Preferences)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        data = data or {}
        return cls(
            identity=Identity(**_pick(data.get("identity"), Identity)),
            summary=data.get("summary", "") or "",
            experience=[Experience(**_pick(x, Experience)) for x in (data.get("experience") or [])],
            education=[Education(**_pick(x, Education)) for x in (data.get("education") or [])],
            skills=[Skill(**_pick(x, Skill)) for x in (data.get("skills") or [])],
            preferences=Preferences(**_pick(data.get("preferences"), Preferences)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_text(self) -> str:
        """Compact text rendering for prompts (cheaper than dumping JSON)."""
        lines = [f"Name: {self.identity.name}", f"Location: {self.identity.location}"]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        if self.experience:
            lines.append("Experience:")
            for e in self.experience:
                lines.append(f"- {e.role} @ {e.org} ({e.start}–{e.end})")
                for b in e.bullets:
                    lines.append(f"    • {b}")
        if self.skills:
            lines.append("Skills: " + ", ".join(s.name for s in self.skills if s.name))
        if self.education:
            lines.append(
                "Education: "
                + "; ".join(f"{e.degree}, {e.institution} ({e.year})" for e in self.education)
            )
        return "\n".join(lines)


def _pick(data: Any, cls: type) -> dict[str, Any]:
    """Keep only keys that are fields of `cls`, so extra LLM keys don't break construction."""
    if not isinstance(data, dict):
        return {}
    valid = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    return {k: v for k, v in data.items() if k in valid}
