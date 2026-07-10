"""Job Scout (Pillar 2a) — discover roles from a job-data provider.

Discovery only; no outward action. Delegates to a JobDataProvider connector so
the data source is swappable (stub -> Jooble/web/ATS board).
"""
from __future__ import annotations

from ..profile import Preferences
from ..tools.job_data import Job, JobDataProvider


def _location_ok(job: Job, wanted: str) -> bool:
    """Keep a job only if it's in the user's area or remote. Postings with an
    unknown location are dropped when an area filter is active — the user asked
    for same-area-or-remote, and 'unknown' is neither.

    ponytail: naive substring match ("york" would hit "Yorkshire"); good enough
    for city-level filtering — upgrade path is a geocoding lookup."""
    loc = (job.location or "").lower()
    if job.remote or "remote" in loc:
        return True
    wanted = wanted.lower().strip()
    tokens = [t.strip(",.()") for t in wanted.split()]
    return bool(wanted) and bool(loc) and (wanted in loc or any(
        tok in loc for tok in tokens if len(tok) >= 4
    ))


class JobScout:
    name = "Job Scout"
    risk = "low"

    def __init__(self, provider: JobDataProvider):
        self.provider = provider

    def discover(self, preferences: Preferences, limit: int = 10) -> list[Job]:
        jobs = self.provider.search(preferences, limit=limit)
        if preferences.locations:
            jobs = [j for j in jobs if _location_ok(j, preferences.locations[0])]
        return jobs
