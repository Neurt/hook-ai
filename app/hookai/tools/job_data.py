"""Job-data connector (Pillar 2a).

The real implementation should call a *legitimate* job API (Adzuna, ZipRecruiter,
TheirStack) or a public ATS board (Greenhouse, Lever, Ashby) — never scrape a
logged-in platform. See docs/feasibility.md. The stub returns sample postings so
the rest of the pipeline runs without a job-API key.
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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..profile import Preferences

if TYPE_CHECKING:
    from ..llm import LLM


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    description: str
    remote: bool = False
    salary: str = ""
    url: str = ""
    source: str = "stub"
    posted: str = ""  # ISO-ish posting/update date when the source provides one


@runtime_checkable
class JobDataProvider(Protocol):
    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]: ...


class StubJobDataProvider:
    """Returns canned sample jobs, lightly filtered by preferences."""

    def __init__(self, jobs: list[Job] | None = None):
        self._jobs = jobs if jobs is not None else list(_SAMPLE)

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        wanted = [t.lower() for t in (preferences.titles or [])]
        out: list[Job] = []
        for job in self._jobs:
            if preferences.remote and not job.remote:
                continue
            if wanted and not any(w in job.title.lower() for w in wanted):
                continue
            out.append(job)
        # fall back to everything if filters excluded all (keeps demo non-empty)
        return (out or self._jobs)[:limit]


_SAMPLE: list[Job] = [
    Job(
        id="j1",
        title="Senior Backend Engineer",
        company="Northwind Labs",
        location="Remote (EU)",
        remote=True,
        description="Python, FastAPI, PostgreSQL, AWS. Build data-intensive APIs at scale. Kubernetes a plus.",
        salary="€70k–90k",
        url="https://example.com/jobs/j1",
    ),
    Job(
        id="j2",
        title="Platform Engineer",
        company="Helios Systems",
        location="Berlin",
        remote=False,
        description="Kubernetes, Terraform, Go, CI/CD. Own the internal developer platform.",
        salary="€80k–100k",
        url="https://example.com/jobs/j2",
    ),
    Job(
        id="j3",
        title="Machine Learning Engineer",
        company="Quanta AI",
        location="Remote (Global)",
        remote=True,
        description="PyTorch, Python, model serving, RAG pipelines and evaluation. MLOps experience valued.",
        salary="$120k–150k",
        url="https://example.com/jobs/j3",
    ),
]


# ── Adzuna (live job data, free tier) ───────────────────────────────────────
# Register free at https://developer.adzuna.com/ and set in app/.env:
#   ADZUNA_APP_ID, ADZUNA_APP_KEY      (optional: ADZUNA_COUNTRY, default "gb")
# Adzuna covers a fixed set of countries (e.g. gb, us, au, ca, de, fr, in, sg,
# nl, it, es, pl, br, mx, nz, at, be, ch, za). Set ADZUNA_COUNTRY to one of them
# and make `where` match that country. Verify the current list in Adzuna's docs.

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Strip HTML tags + unescape entities (Adzuna snippets can contain both)."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _format_salary(low, high, predicted) -> str:
    if not low and not high:
        return ""

    def fmt(value) -> str:
        return f"{int(float(value)):,}" if value else "?"

    suffix = " (est.)" if str(predicted) in ("1", "True", "true") else ""
    if low and high:
        return f"{fmt(low)}–{fmt(high)}{suffix}"
    return f"{fmt(low or high)}{suffix}"


def _dedupe_jobs(jobs: list["Job"], max_per_company: int | None = None) -> list["Job"]:
    """Drop repeated postings (Adzuna often returns the same role several times).

    Removes exact id repeats, then collapses postings with the same normalized
    title+company+location (keeping the first). With max_per_company set, also caps how
    many postings any one company/recruiter contributes — taming spray-posted listings
    (e.g. a course recruiter posting the same role across every borough)."""
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    company_counts: dict[str, int] = {}
    out: list[Job] = []
    for job in jobs:
        jid = (job.id or "").strip()
        key = " ".join(f"{job.title} {job.company} {job.location}".lower().split())
        if (jid and jid in seen_ids) or key in seen_keys:
            continue
        company = " ".join(job.company.lower().split())
        if max_per_company is not None and company and company_counts.get(company, 0) >= max_per_company:
            continue
        if jid:
            seen_ids.add(jid)
        seen_keys.add(key)
        company_counts[company] = company_counts.get(company, 0) + 1
        out.append(job)
    return out


class AdzunaError(RuntimeError):
    pass


# Adzuna's supported countries: name/alias -> country code. Codes themselves
# ("us", "gb") only match as the LAST comma-separated token of the location
# ("New York, US") — bare "us"/"in" mid-string are common English words.
_ADZUNA_COUNTRIES = {
    "united kingdom": "gb", "uk": "gb", "britain": "gb", "england": "gb", "scotland": "gb", "wales": "gb",
    "united states": "us", "usa": "us", "america": "us",
    "austria": "at", "australia": "au", "belgium": "be", "brazil": "br", "canada": "ca",
    "switzerland": "ch", "germany": "de", "spain": "es", "france": "fr", "india": "in",
    "italy": "it", "mexico": "mx", "netherlands": "nl", "new zealand": "nz", "poland": "pl",
    "singapore": "sg", "south africa": "za",
}
_ADZUNA_CODES = set(_ADZUNA_COUNTRIES.values())


def _resolve_adzuna_country(location: str, default: str) -> str:
    """Infer the Adzuna country code from a search location ("Berlin, Germany" -> de),
    falling back to `default` when the country is unsupported or absent.
    ponytail: name matching only — a bare city ("London") won't resolve; upgrade
    path is a city-to-country lookup."""
    loc = (location or "").lower()
    for name, code in _ADZUNA_COUNTRIES.items():
        if re.search(rf"\b{re.escape(name)}\b", loc):
            return code
    last = loc.rsplit(",", 1)[-1].strip()
    if last in _ADZUNA_CODES or last in _ADZUNA_COUNTRIES:
        return _ADZUNA_COUNTRIES.get(last, last)
    return default


class AdzunaJobDataProvider:
    """Live job search via the Adzuna API. Implements the JobDataProvider protocol."""

    BASE = "https://api.adzuna.com/v1/api/jobs"

    def __init__(self, app_id: str, app_key: str, country: str = "gb", timeout: float = 20.0):
        if not app_id or not app_key:
            raise AdzunaError(
                "Adzuna needs app_id and app_key. Register free at "
                "https://developer.adzuna.com/ then set ADZUNA_APP_ID / ADZUNA_APP_KEY."
            )
        self.app_id = app_id
        self.app_key = app_key
        self.country = (country or "gb").lower()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "AdzunaJobDataProvider":
        return cls(
            app_id=(os.getenv("ADZUNA_APP_ID") or "").strip(),
            app_key=(os.getenv("ADZUNA_APP_KEY") or "").strip(),
            country=(os.getenv("ADZUNA_COUNTRY") or "gb").strip() or "gb",
        )

    def _build_url(self, preferences: Preferences, limit: int, page: int | None = None) -> str:
        if page is None:
            page = max(1, int(getattr(preferences, "page", 1) or 1))
        what = " ".join(preferences.titles or []).strip()
        if preferences.remote:
            what = (what + " remote").strip()  # Adzuna has no boolean remote filter
        params: dict[str, object] = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": max(1, min(int(limit), 50)),
            "content-type": "application/json",
        }
        if what:
            params["what"] = what
        if preferences.locations:
            params["where"] = preferences.locations[0]
        if preferences.salary_floor:
            params["salary_min"] = int(preferences.salary_floor)
        # Country is dynamic per query: inferred from the search location, with
        # the configured country (ADZUNA_COUNTRY) as fallback.
        country = _resolve_adzuna_country(
            preferences.locations[0] if preferences.locations else "", self.country
        )
        return f"{self.BASE}/{country}/search/{page}?" + urllib.parse.urlencode(params)

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        # Over-fetch (capped at Adzuna's 50/page) so dedupe still leaves ~limit unique jobs.
        fetch = min(max(limit * 2, limit), 50)
        data = self._get(self._build_url(preferences, fetch))
        results = data.get("results", []) if isinstance(data, dict) else []
        jobs = [self._to_job(r) for r in results]
        return _dedupe_jobs(jobs, max_per_company=2)[:limit]

    def _get(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": "HookAI/0.1 (+job-scout)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise AdzunaError(f"Adzuna HTTP {exc.code} (country={self.country!r}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise AdzunaError(f"Adzuna request failed: {exc.reason}") from exc

    @staticmethod
    def _to_job(result: dict) -> Job:
        title = _clean(result.get("title", ""))
        description = _clean(result.get("description", ""))
        blob = f"{title} {description}".lower()
        return Job(
            id=str(result.get("id", "")),
            title=title,
            company=(result.get("company") or {}).get("display_name", ""),
            location=(result.get("location") or {}).get("display_name", ""),
            description=description,
            remote=("remote" in blob or "work from home" in blob),
            salary=_format_salary(
                result.get("salary_min"), result.get("salary_max"), result.get("salary_is_predicted")
            ),
            url=result.get("redirect_url", "") or "",
            source="adzuna",
        )


# ── Jooble (live job data, free key, ~50+ countries) ────────────────────────
# Register free at https://jooble.org/api/about → key emailed. One endpoint,
# country targeted via the `location` field ("Jakarta", "Berlin", "New York").
# NOTE: free-tier rate limits are NOT published by Jooble — verify live.
class JoobleError(RuntimeError):
    pass


class JoobleJobDataProvider:
    """Live international job search via the Jooble REST API.
    Implements the JobDataProvider protocol."""

    BASE = "https://jooble.org/api/"

    def __init__(self, api_key: str, timeout: float = 20.0):
        if not api_key:
            raise JoobleError(
                "Jooble needs an API key. Register free at https://jooble.org/api/about "
                "then set JOOBLE_API_KEY."
            )
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "JoobleJobDataProvider":
        return cls(api_key=(os.getenv("JOOBLE_API_KEY") or "").strip())

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        keywords = " ".join(preferences.titles or []).strip()
        if preferences.remote:
            keywords = (keywords + " remote").strip()
        body: dict[str, object] = {
            "keywords": keywords or "jobs",
            "location": (preferences.locations[0] if preferences.locations else "").strip(),
            "ResultOnPage": max(1, min(int(limit) * 2, 50)),  # over-fetch for dedupe
            "page": max(1, int(getattr(preferences, "page", 1) or 1)),
        }
        if preferences.salary_floor:
            body["salary"] = int(preferences.salary_floor)
        data = self._post(body)
        results = data.get("jobs", []) if isinstance(data, dict) else []
        jobs = [self._to_job(r) for r in results]
        return _dedupe_jobs(jobs, max_per_company=2)[:limit]

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.BASE}{self.api_key}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "HookAI/0.1 (+job-scout)"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise JoobleError(f"Jooble HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise JoobleError(f"Jooble request failed: {exc}") from exc

    @staticmethod
    def _to_job(result: dict) -> Job:
        title = _clean(str(result.get("title", "")))
        location = _clean(str(result.get("location", "")))
        snippet = _clean(str(result.get("snippet", "")))[:600]
        blob = f"{title} {location} {snippet}".lower()
        return Job(
            id=f"joo-{result.get('id', '')}",
            title=title,
            company=str(result.get("company", "") or ""),
            location=location,
            description=snippet,
            remote=("remote" in blob or "work from home" in blob),
            salary=str(result.get("salary", "") or ""),
            url=str(result.get("link", "") or ""),
            source="jooble",
            posted=str(result.get("updated", "") or ""),
        )


# ── Remotive (live remote jobs, keyless public API) ─────────────────────────
# Terms (from the API itself): link back to the Remotive job URL and attribute
# Remotive as the source — we keep `url` and set source="remotive" on every job.
class RemotiveJobDataProvider:
    """Global remote jobs via Remotive's public API (no key). Remote-only listings,
    so it's a *supplement* to Adzuna, not a replacement."""

    BASE = "https://remotive.com/api/remote-jobs"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        what = " ".join(preferences.titles or []).strip()
        params: dict[str, object] = {"limit": max(1, min(int(limit), 50))}
        if what:
            params["search"] = what
        url = f"{self.BASE}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "HookAI/0.1 (+job-scout)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError):
            return []  # supplemental source: fail soft, Adzuna/stub still answer
        jobs = []
        for r in (data.get("jobs") or [])[:limit]:
            jobs.append(Job(
                id=f"rem-{r.get('id', '')}",
                title=_clean(r.get("title", "")),
                company=r.get("company_name", ""),
                location=f"Remote ({r.get('candidate_required_location', 'Worldwide')})",
                description=_clean(r.get("description", ""))[:600],
                remote=True,
                salary=r.get("salary", "") or "",
                url=r.get("url", "") or "",
                source="remotive",
            ))
        return jobs


# Aggregator SEARCH/landing pages masquerading as postings (the LLM sometimes
# extracts them anyway — this deterministic guard drops them). Individual posting
# URLs (greenhouse/lever/ashby, company career pages, indeed /viewjob) pass.
_SEARCH_PAGE_RE = re.compile(
    r"indeed\.[a-z.]+/(q-|jobs\b|m/jobs)"        # indeed search (incl. localized q-…-lowongan.html)
    r"|linkedin\.com/jobs/search"
    r"|glassdoor\.[a-z.]+/(Job|Search)/"
    r"|jobstreet\.[a-z.]+/.+-jobs\b"
    r"|[?&]q=",                                   # generic ?q= search query pages
    re.IGNORECASE,
)


def _is_search_page(url: str) -> bool:
    return bool(_SEARCH_PAGE_RE.search(url or ""))


class WebSearchJobDataProvider:
    """Keyless, quota-free job discovery via a self-hosted SearXNG meta-search.

    Web results are messy (board landing pages, aggregators, noise), so the LLM
    extracts only concrete individual postings and skips the rest — plus a
    deterministic URL guard against aggregator search pages. Fans out a generic
    query AND an ATS-host dork (greenhouse/lever/ashby host individual postings,
    so those hits are high-precision). A supplement, not a replacement."""

    def __init__(self, llm: "LLM", base_url: str, timeout: float = 25.0):
        self.llm = llm
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        what = " ".join(preferences.titles or []).strip() or "jobs"
        where = (preferences.locations[0] if preferences.locations else "").strip()
        remote = "remote" if preferences.remote else ""
        tail = " ".join(x for x in [where, remote] if x)
        queries = [
            f"{what} job posting {tail}".strip(),
            # ATS dork: these hosts serve one posting per URL — high precision.
            f"site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com {what} {tail}".strip(),
        ]
        page = max(1, int(getattr(preferences, "page", 1) or 1))
        merged: list[dict] = []
        seen_urls: set[str] = set()
        for query in queries:
            for r in self._search_web(query, page=page):
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    merged.append(r)
        if not merged:
            return []
        return self._extract_jobs(merged[:30], limit)

    def _search_web(self, query: str, page: int = 1) -> list[dict]:
        # time_range keeps results fresh — a month-old posting is usually still open.
        params: dict[str, object] = {"q": query, "format": "json", "time_range": "month"}
        if page > 1:
            params["pageno"] = page
        url = f"{self.base_url}/search?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "HookAI/0.1 (+job-scout)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError):
            return []
        out = []
        for item in (data.get("results") or [])[:20]:
            out.append({"title": _clean(item.get("title", "")), "url": item.get("url", ""),
                        "content": _clean(item.get("content", ""))[:300]})
        return out

    def _extract_jobs(self, results: list[dict], limit: int) -> list[Job]:
        listing = "\n".join(
            f"[{i}] {r['title']} | {r['url']} | {r['content']}" for i, r in enumerate(results)
        )
        system = (
            "[task:extract_web_jobs] These are web search results. Extract ONLY concrete "
            "individual job postings — a specific role at a specific named company. SKIP job-board "
            "search/landing pages (e.g. '9,000+ jobs'), aggregators, articles, salary guides, and "
            "anything not a single posting. Return ONLY JSON: "
            '{"jobs":[{"title":"","company":"","location":"","url":""}]}. '
            "Use each result's exact url. Infer company/location from the title or snippet when clear; "
            "leave blank if unknown. If none are concrete postings, return an empty list."
        )
        data = self.llm.complete_json(system, f"RESULTS:\n{listing}", max_tokens=1500)
        raw = data.get("jobs", []) if isinstance(data, dict) else []
        jobs: list[Job] = []
        for j in raw:
            if not isinstance(j, dict) or not j.get("title") or not j.get("url"):
                continue
            if _is_search_page(j["url"]):  # aggregator search page, not a posting
                continue
            jobs.append(Job(id=str(j["url"])[:80], title=j["title"], company=j.get("company", ""),
                            location=j.get("location", ""), description="", url=j["url"], source="web"))
        return jobs[:limit]


class FallbackJobDataProvider:
    """Tries providers IN ORDER, returns the first non-empty result set. Later
    providers are never called when an earlier one answers — conserves metered
    quotas (Jooble ~500/day, Adzuna ~250/day). Errors (quota 429, network) fall
    through to the next source. Implements the JobDataProvider protocol."""

    def __init__(self, providers: list[JobDataProvider]):
        self.providers = providers

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        for provider in self.providers:
            try:
                jobs = provider.search(preferences, limit=limit)
            except Exception:
                continue  # exhausted/dead source: fall through
            if jobs:
                return jobs
        return []


class MultiJobDataProvider:
    """Aggregates several providers, merges + dedupes. Ranking happens downstream
    (MatchRank), so irrelevant listings from any one source get scored out."""

    def __init__(self, providers: list[JobDataProvider]):
        self.providers = providers

    def search(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        merged: list[Job] = []
        for provider in self.providers:
            try:
                merged.extend(provider.search(preferences, limit=limit))
            except Exception:
                continue  # one dead source shouldn't kill the search
        return _dedupe_jobs(merged, max_per_company=2)[: limit * 2]


def make_provider_from_env(llm: "LLM | None" = None, verbose: bool = False) -> JobDataProvider:
    """Build the job-source FALLBACK chain from env, in quota-conserving order:
    Jooble (international, ~500/day) -> Adzuna (dynamic country, ~250/day) ->
    Remotive (keyless, off by default) -> SearXNG web (keyless). First source
    with results answers; later ones aren't called. Stub when nothing live."""
    providers: list[JobDataProvider] = []
    sources: list[str] = []
    jooble_key = (os.getenv("JOOBLE_API_KEY") or "").strip()
    if jooble_key:
        providers.append(JoobleJobDataProvider(jooble_key))
        sources.append("Jooble(intl)")
    if (os.getenv("ADZUNA_APP_ID") or "").strip() and (os.getenv("ADZUNA_APP_KEY") or "").strip():
        providers.append(AdzunaJobDataProvider.from_env())
        sources.append("Adzuna(dynamic)")
    if (os.getenv("HOOKAI_REMOTIVE", "1").strip().lower() not in ("0", "false", "off")):
        providers.append(RemotiveJobDataProvider())
        sources.append("Remotive(remote)")
    searx_url = (os.getenv("SEARXNG_URL") or "").strip()
    if searx_url and llm is not None:
        providers.append(WebSearchJobDataProvider(llm, searx_url))
        sources.append("Web(searxng)")
    if not providers:
        if verbose:
            print("[job source] stub sample data — set JOOBLE_API_KEY in app/.env for live jobs")
        return StubJobDataProvider()
    if verbose:
        print(f"[job source] {' > '.join(sources)} (fallback order)")
    return providers[0] if len(providers) == 1 else FallbackJobDataProvider(providers)
