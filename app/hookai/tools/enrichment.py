"""Contact-enrichment connector (Pillar 3).

Real implementation: a compliant provider (Hunter.io, Apollo) that indexes
PUBLIC professional emails and records provenance. Keep the source URL for
GDPR/CCPA accountability. The stub returns a clearly-fake placeholder contact.
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..llm import LLM
    from .job_data import Job


@dataclass
class Contact:
    name: str
    title: str
    email: str
    company: str
    source: str = "stub"
    public_source_url: str = ""  # provenance — required for compliant real providers


@runtime_checkable
class EnrichmentProvider(Protocol):
    def find_hiring_contact(
        self, company: str, role_title: str, posting: "Job | None" = None
    ) -> Optional[Contact]: ...


class StubEnrichmentProvider:
    """Placeholder. Does NOT look anyone up — returns an obviously-fake address."""

    def find_hiring_contact(
        self, company: str, role_title: str, posting: "Job | None" = None
    ) -> Optional[Contact]:
        handle = "".join(ch for ch in company.lower() if ch.isalnum()) or "company"
        return Contact(
            name="(unknown — verify before contacting)",
            title=f"Hiring Manager · {role_title}",
            email=f"careers@{handle}.example",
            company=company,
            source="stub",
            public_source_url="",
        )


class HunterError(RuntimeError):
    pass


class HunterEnrichmentProvider:
    """Find a public hiring/HR work email via Hunter.io Domain Search.

    Hunter indexes only publicly-available professional emails and is GDPR/CCPA-ready;
    we keep the source URL for provenance. Free key (25 searches/mo) at
    https://hunter.io/api-keys . Implements the EnrichmentProvider protocol."""

    BASE = "https://api.hunter.io/v2/domain-search"

    def __init__(self, api_key: str, timeout: float = 20.0):
        if not api_key:
            raise HunterError(
                "Hunter needs an API key. Set HUNTER_API_KEY (free at https://hunter.io/api-keys)."
            )
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "HunterEnrichmentProvider":
        return cls(api_key=(os.getenv("HUNTER_API_KEY") or "").strip())

    def find_hiring_contact(
        self, company: str, role_title: str, posting: "Job | None" = None
    ) -> Optional[Contact]:
        # Prefer HR/recruiting contacts; fall back to any personal email at the company.
        data = self._search(company, department="hr")
        if not self._emails(data):
            data = self._search(company, department=None)
        return self._pick_contact(data, company, role_title)

    def _search(self, company: str, department: Optional[str]) -> dict:
        params: dict[str, object] = {
            "company": company,
            "type": "personal",
            "limit": 10,
            "api_key": self.api_key,
        }
        if department:
            params["department"] = department
        url = f"{self.BASE}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "HookAI/0.1 (+job-scout)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise HunterError(f"Hunter HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HunterError(f"Hunter request failed: {exc.reason}") from exc

    @staticmethod
    def _emails(data: dict) -> list[dict]:
        return ((data or {}).get("data") or {}).get("emails") or []

    @staticmethod
    def _score(email: dict) -> tuple:
        position = (email.get("position") or "").lower()
        department = (email.get("department") or "").lower()
        seniority = (email.get("seniority") or "").lower()
        confidence = email.get("confidence") or 0
        hire_words = ("recruit", "talent", "people", "hiring", "human resources", "staffing", "hr")
        is_hiring = department == "hr" or any(word in position for word in hire_words)
        seniority_rank = {"executive": 2, "senior": 1}.get(seniority, 0)
        return (1 if is_hiring else 0, seniority_rank, confidence)

    @classmethod
    def _pick_contact(cls, data: dict, company: str, role_title: str) -> Optional[Contact]:
        emails = cls._emails(data)
        if not emails:
            return None
        best = max(emails, key=cls._score)
        organization = (data.get("data") or {}).get("organization") or company
        name = f"{best.get('first_name') or ''} {best.get('last_name') or ''}".strip() or "Hiring team"
        sources = best.get("sources") or []
        return Contact(
            name=name,
            title=best.get("position") or f"Hiring contact · {role_title}",
            email=best.get("value", ""),
            company=organization,
            source="hunter",
            public_source_url=(sources[0].get("uri", "") if sources else ""),
        )


_TAG_RE = re.compile(r"<[^>]+>")
# Real address, structurally; used to reject the LLM's guesses/placeholders.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Same, but for pulling addresses out of free-flowing posting text.
_EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "yourcompany.com",
                        "company.com", "domain.com", "email.com")
# Mailbox names that signal a hiring/recruiting inbox, preferred over generic ones.
_HIRING_LOCALPARTS = ("recruit", "talent", "career", "job", "hiring", "people",
                      "apply", "resume", "cv", "hr")


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _looks_like_email(value: str) -> bool:
    """True only for a plausibly-real address. Rejects malformed strings, RFC-2606
    example TLDs, and common placeholder domains the LLM invents when it's guessing."""
    value = (value or "").strip().lower()
    if not _EMAIL_RE.match(value):
        return False
    domain = value.rsplit("@", 1)[-1]
    if domain.endswith(".example") or domain.split(".")[-1] == "example":
        return False
    return domain not in _PLACEHOLDER_DOMAINS


def _email_from_posting(text: str) -> str:
    """Return the best real contact email that literally appears in a posting, else ''.
    Prefers an obviously hiring-related mailbox (recruiting@, careers@, jobs@…) over a
    generic one (support@, info@) when the posting lists several."""
    valid = [m.group(0) for m in _EMAIL_IN_TEXT_RE.finditer(text or "") if _looks_like_email(m.group(0))]
    if not valid:
        return ""
    for email in valid:
        localpart = email.split("@", 1)[0].lower()
        if any(word in localpart for word in _HIRING_LOCALPARTS):
            return email
    return valid[0]


class WebSearchEnrichmentProvider:
    """Keyless, quota-free contact discovery — from the job posting first.

    Order: (1) if the posting text itself names an email (many list "apply to X@…"),
    use it directly, provenance = the posting URL — free, no network; (2) otherwise
    search a self-hosted SearXNG for the company's PUBLIC careers/recruiting email and
    have the LLM extract a single real address + the page it came from (GDPR provenance).
    Lower precision than a curated provider, but keyless and unquota'd. Implements
    EnrichmentProvider."""

    def __init__(self, llm: "LLM", base_url: str, timeout: float = 25.0):
        self.llm = llm
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def find_hiring_contact(
        self, company: str, role_title: str, posting: "Job | None" = None
    ) -> Optional[Contact]:
        company = (company or (getattr(posting, "company", "") if posting else "") or "").strip()
        # 1) The contact from the posting itself, if it lists one.
        if posting is not None:
            email = _email_from_posting(getattr(posting, "description", "") or "")
            if email:
                return Contact(
                    name="Hiring team",
                    title=f"Hiring contact · {role_title}",
                    email=email,
                    company=company,
                    source="posting",
                    public_source_url=(getattr(posting, "url", "") or ""),
                )
        # 2) Fall back to a web search on the company.
        if not company:
            return None
        query = f'"{company}" careers OR recruiting OR jobs contact email'
        results = self._search_web(query)
        if not results:
            return None
        return self._extract_contact(results, company, role_title)

    def _search_web(self, query: str) -> list[dict]:
        url = f"{self.base_url}/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
        request = urllib.request.Request(url, headers={"User-Agent": "HookAI/0.1 (+job-scout)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError):
            return []  # fallback source: fail soft, the stub still answers downstream
        out = []
        for item in (data.get("results") or [])[:20]:
            out.append({"title": _clean(item.get("title", "")), "url": item.get("url", ""),
                        "content": _clean(item.get("content", ""))[:300]})
        return out

    def _extract_contact(self, results: list[dict], company: str, role_title: str) -> Optional[Contact]:
        listing = "\n".join(
            f"[{i}] {r['title']} | {r['url']} | {r['content']}" for i, r in enumerate(results)
        )
        system = (
            "[task:extract_contact_email] These are web search results for a company's public "
            f"contact/careers pages. Find ONE real, publicly-listed work email for reaching {company}'s "
            "hiring/recruiting team (e.g. a careers@, jobs@, recruiting@, or a named recruiter's address) "
            "that ACTUALLY APPEARS in the results. Return ONLY JSON: "
            '{"email":"","name":"","title":"","source_url":""}. '
            "Copy source_url from the exact result the email appears on. Do NOT guess or construct an "
            "address, do NOT return example/placeholder domains. If no real email is present, return "
            '{"email":""}.'
        )
        try:
            data = self.llm.complete_json(system, f"COMPANY: {company}\nRESULTS:\n{listing}", max_tokens=400)
        except Exception:
            return None
        email = (data.get("email") or "").strip() if isinstance(data, dict) else ""
        if not _looks_like_email(email):
            return None
        return Contact(
            name=(data.get("name") or "Hiring team").strip(),
            title=(data.get("title") or f"Hiring contact · {role_title}").strip(),
            email=email,
            company=company,
            source="web",
            public_source_url=(data.get("source_url") or "").strip(),
        )


class ChainedEnrichmentProvider:
    """Tries providers in order and returns the first real Contact — e.g. keyless web
    search first (contact from the posting), then Hunter only if it's opted in. A
    dead/exhausted provider is skipped, not fatal. Implements EnrichmentProvider."""

    def __init__(self, providers: list[EnrichmentProvider]):
        self.providers = providers

    def find_hiring_contact(
        self, company: str, role_title: str, posting: "Job | None" = None
    ) -> Optional[Contact]:
        for provider in self.providers:
            try:
                contact = provider.find_hiring_contact(company, role_title, posting)
            except Exception:
                continue  # one exhausted/dead source shouldn't sink the lookup
            if contact is not None:
                return contact
        return None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "on", "yes")


def make_enrichment_from_env(llm: "LLM | None" = None, verbose: bool = False) -> EnrichmentProvider:
    """Build the contact-lookup chain from env. Default is keyless SearXNG web search
    (if SEARXNG_URL + an llm) — it reads the contact from the job posting first, then
    the company's public pages. Hunter.io is OPT-IN via HOOKAI_HUNTER=1 (its 25/month
    quota isn't spent by default); when enabled it's a secondary source after web.
    Falls back to the stub placeholder when nothing live is available."""
    providers: list[EnrichmentProvider] = []
    sources: list[str] = []
    searx_url = (os.getenv("SEARXNG_URL") or "").strip()
    if searx_url and llm is not None:
        providers.append(WebSearchEnrichmentProvider(llm, searx_url))
        sources.append("Web(searxng)")
    if _truthy(os.getenv("HOOKAI_HUNTER")):
        key = (os.getenv("HUNTER_API_KEY") or "").strip()
        if key:
            providers.append(HunterEnrichmentProvider(key))
            sources.append("Hunter.io")
    if not providers:
        if verbose:
            print("[contacts] stub placeholder — set SEARXNG_URL (+llm) for real public emails")
        return StubEnrichmentProvider()
    if verbose:
        print(f"[contacts] {' + '.join(sources)}")
    return providers[0] if len(providers) == 1 else ChainedEnrichmentProvider(providers)
