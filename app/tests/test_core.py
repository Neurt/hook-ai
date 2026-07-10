"""Unit tests for Hook AI's pure logic (no network, no key, no FastAPI).

Run:  cd app && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from hookai.gates import ApproveAllGate, AutoBlockGate, OutwardAction
from hookai.heuristics import looks_like_cv
from hookai.llm import FakeLLM, LLMError, LLMParseError, extract_json
from hookai.profile import Profile
from hookai.store import SessionStore
from hookai.tools.docgen import _needs_unicode_font, parse_cv_markdown
from hookai.profile import Preferences
from hookai.tools.enrichment import (
    ChainedEnrichmentProvider,
    Contact,
    StubEnrichmentProvider,
    WebSearchEnrichmentProvider,
    make_enrichment_from_env,
)
from hookai.agents.job_scout import JobScout
from hookai.tools.job_data import (
    AdzunaJobDataProvider,
    FallbackJobDataProvider,
    Job,
    JoobleJobDataProvider,
    MultiJobDataProvider,
    WebSearchJobDataProvider,
    _dedupe_jobs,
    _is_search_page,
    _resolve_adzuna_country,
    make_provider_from_env,
)


class TestExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_wrapped(self):
        self.assertEqual(extract_json('Sure! Here it is: {"a": 1} hope that helps'), {"a": 1})

    def test_array(self):
        self.assertEqual(extract_json("[1, 2]"), [1, 2])

    def test_malformed_raises_parse_error(self):
        with self.assertRaises(LLMParseError):
            extract_json("{broken")
        with self.assertRaises(LLMParseError):
            extract_json("no json here at all")


class _FlakyLLM(FakeLLM):
    """Returns invalid JSON for the first n-1 calls, then valid."""

    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times
        self.n = 0

    def chat(self, system, user, **kw):
        self.n += 1
        return "{bad" if self.n <= self.fail_times else '{"ok": true}'


class _ApiDownLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.n = 0

    def chat(self, system, user, **kw):
        self.n += 1
        raise LLMError("401 bad key")


class TestCompleteJsonRetry(unittest.TestCase):
    def test_parse_error_is_retried(self):
        llm = _FlakyLLM(fail_times=2)
        self.assertEqual(llm.complete_json("s", "u"), {"ok": True})
        self.assertEqual(llm.n, 3)

    def test_exhausted_retries_raise_parse_error(self):
        llm = _FlakyLLM(fail_times=99)
        with self.assertRaises(LLMParseError):
            llm.complete_json("s", "u", retries=2)
        self.assertEqual(llm.n, 3)  # exactly retries + 1 attempts

    def test_api_error_is_not_retried(self):
        llm = _ApiDownLLM()
        with self.assertRaises(LLMError):
            llm.complete_json("s", "u")
        self.assertEqual(llm.n, 1)  # no paid retries on auth/model failures


def _job(i, title="Data Analyst", company="ITOL", location="London"):
    return Job(id=str(i), title=title, company=company, location=location, description="")


class TestDedupeJobs(unittest.TestCase):
    def test_id_and_key_dupes_removed(self):
        jobs = [_job(1), _job(1), _job(2)]  # id dupe + same title/company/location
        self.assertEqual(len(_dedupe_jobs(jobs)), 1)

    def test_different_locations_survive(self):
        jobs = [_job(1, location="London"), _job(2, location="Bromley")]
        self.assertEqual(len(_dedupe_jobs(jobs)), 2)

    def test_per_company_cap(self):
        jobs = [_job(i, location=f"Loc{i}") for i in range(5)] + [_job(9, company="Other")]
        out = _dedupe_jobs(jobs, max_per_company=2)
        self.assertEqual(sum(1 for j in out if j.company == "ITOL"), 2)
        self.assertEqual(sum(1 for j in out if j.company == "Other"), 1)


class _ListProvider:
    def __init__(self, jobs):
        self.jobs = jobs

    def search(self, preferences, limit=10):
        return self.jobs


class _BrokenProvider:
    def search(self, preferences, limit=10):
        raise RuntimeError("source down")


class TestMultiProvider(unittest.TestCase):
    def test_merges_and_dedupes_across_sources(self):
        a = _ListProvider([_job(1, company="Acme"), _job(2, company="Beta")])
        b = _ListProvider([_job(1, company="Acme"), _job(3, company="Gamma")])  # id 1 dupe
        out = MultiJobDataProvider([a, b]).search(None)
        self.assertEqual(sorted(j.id for j in out), ["1", "2", "3"])

    def test_one_dead_source_fails_soft(self):
        alive = _ListProvider([_job(1, company="Acme")])
        out = MultiJobDataProvider([_BrokenProvider(), alive]).search(None)
        self.assertEqual([j.id for j in out], ["1"])


class TestWebSearchProvider(unittest.TestCase):
    def test_llm_extraction_filters_and_tags(self):
        class _NoNet(WebSearchJobDataProvider):
            def _search_web(self, query, page=1):  # skip SearXNG, feed canned results
                return [{"title": "t", "url": "http://a", "content": "c"}]

        llm = FakeLLM(scripted={"task:extract_web_jobs":
            '{"jobs":[{"title":"Data Analyst","company":"Acme","location":"NY",'
            '"url":"https://acme.com/jobs/1"},{"title":"","url":"http://bad"}]}'})
        jobs = _NoNet(llm, "http://searx").search(Preferences(titles=["data analyst"]), limit=5)
        self.assertEqual(len(jobs), 1)  # the entry with no title is dropped
        self.assertEqual(jobs[0].company, "Acme")
        self.assertEqual(jobs[0].source, "web")

    def test_empty_search_returns_nothing(self):
        class _Empty(WebSearchJobDataProvider):
            def _search_web(self, query, page=1):
                return []

        self.assertEqual(_Empty(FakeLLM(), "http://searx").search(Preferences()), [])

    def test_search_page_urls_detected(self):
        # The exact URL from the live bug: an Indeed search page sold as a posting.
        self.assertTrue(_is_search_page("https://id.indeed.com/q-remote-internship,-backend-lowongan.html"))
        self.assertTrue(_is_search_page("https://www.indeed.com/jobs?q=backend&l=Jakarta"))
        self.assertTrue(_is_search_page("https://www.linkedin.com/jobs/search?keywords=backend"))
        self.assertTrue(_is_search_page("https://www.glassdoor.com/Job/jakarta-backend-jobs-SRCH_IL.0,7.htm"))
        self.assertTrue(_is_search_page("https://www.jobstreet.co.id/id/backend-developer-jobs"))
        # Real individual postings must pass.
        self.assertFalse(_is_search_page("https://boards.greenhouse.io/acme/jobs/4012345"))
        self.assertFalse(_is_search_page("https://jobs.lever.co/acme/1234-backend-developer"))
        self.assertFalse(_is_search_page("https://acme.com/careers/backend-developer"))

    def test_extraction_drops_search_page_urls(self):
        # Even when the LLM wrongly extracts an aggregator page, the guard drops it.
        class _NoNet(WebSearchJobDataProvider):
            def _search_web(self, query, page=1):
                return [{"title": "t", "url": "http://a", "content": "c"}]

        llm = FakeLLM(scripted={"task:extract_web_jobs":
            '{"jobs":[{"title":"Back End Developer","company":"ASTRA LAND","location":"Jakarta",'
            '"url":"https://id.indeed.com/q-remote-internship,-backend-lowongan.html"},'
            '{"title":"Backend Dev","company":"Acme","location":"Jakarta",'
            '"url":"https://boards.greenhouse.io/acme/jobs/1"}]}'})
        jobs = _NoNet(llm, "http://searx").search(Preferences(titles=["backend"]), limit=5)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Acme")

    def test_fans_out_multiple_queries_and_merges(self):
        seen: list[str] = []

        class _Track(WebSearchJobDataProvider):
            def _search_web(self, query, page=1):
                seen.append(query)
                return [{"title": f"r{len(seen)}", "url": f"http://r{len(seen)}", "content": ""}]

        llm = FakeLLM(scripted={"task:extract_web_jobs": '{"jobs":[]}'})
        _Track(llm, "http://searx").search(Preferences(titles=["backend developer"],
                                                       locations=["Jakarta"]), limit=5)
        self.assertGreaterEqual(len(seen), 2)          # generic + ATS-dork query
        self.assertTrue(any("greenhouse.io" in q for q in seen))  # dork targets ATS hosts
        self.assertTrue(all("backend developer" in q for q in seen))
        # Merged results from all queries reach the one extraction call.
        user_payload = llm.calls[0][1]
        self.assertIn("http://r1", user_payload)
        self.assertIn("http://r2", user_payload)


class TestJoobleProvider(unittest.TestCase):
    RESPONSE = {
        "totalCount": 2,
        "jobs": [
            {"id": 111, "title": "Backend <b>Developer</b>", "company": "Acme ID",
             "location": "Jakarta, Indonesia", "snippet": "Build <b>APIs</b> with Python…",
             "salary": "Rp 15.000.000", "link": "https://jooble.org/jdp/111", "type": "Full-time",
             "updated": "2026-07-05T10:00:00.000Z"},
            {"id": 222, "title": "Remote Data Engineer", "company": "Globex",
             "location": "Remote", "snippet": "Fully remote role", "salary": "",
             "link": "https://jooble.org/jdp/222", "type": ""},
        ],
    }

    def _provider(self, response):
        class _NoNet(JoobleJobDataProvider):
            def __init__(self):
                super().__init__(api_key="k")
                self.sent = None

            def _post(self, body):
                self.sent = body
                return response

        return _NoNet()

    def test_parses_and_cleans(self):
        provider = self._provider(self.RESPONSE)
        jobs = provider.search(Preferences(titles=["backend"], locations=["Jakarta"]), limit=5)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].title, "Backend Developer")          # HTML stripped
        self.assertEqual(jobs[0].id, "joo-111")
        self.assertEqual(jobs[0].source, "jooble")
        self.assertEqual(jobs[0].url, "https://jooble.org/jdp/111")
        self.assertTrue(jobs[1].remote)                                # "Remote" location detected
        self.assertEqual(provider.sent["location"], "Jakarta")         # location passed through
        self.assertIn("backend", provider.sent["keywords"])

    def test_empty_key_raises(self):
        with self.assertRaises(Exception):
            JoobleJobDataProvider(api_key="")

    def test_bad_response_returns_empty(self):
        provider = self._provider({"unexpected": True})
        self.assertEqual(provider.search(Preferences(titles=["x"])), [])

    def test_page_and_posted_date(self):
        provider = self._provider(self.RESPONSE)
        jobs = provider.search(Preferences(titles=["backend"], page=3), limit=5)
        self.assertEqual(provider.sent["page"], 3)              # pagination pass-through
        self.assertEqual(jobs[0].posted, "2026-07-05T10:00:00.000Z")
        self.assertEqual(jobs[1].posted, "")                    # missing date tolerated


class TestSearchPagination(unittest.TestCase):
    def test_adzuna_url_uses_preferences_page(self):
        provider = AdzunaJobDataProvider(app_id="a", app_key="b", country="us")
        url = provider._build_url(Preferences(titles=["dev"], page=2), 10)
        self.assertIn("/search/2?", url)

    def test_next_search_state_bumps_page_on_repeat(self):
        from hookai.heuristics import next_search_state
        state = {}
        state = next_search_state(state, "backend", "Jakarta", False)
        self.assertEqual(state["page"], 1)                      # first search
        state["shown"] = ["a", "b"]
        state = next_search_state(state, "backend", "Jakarta", False)
        self.assertEqual(state["page"], 2)                      # same query: next page
        self.assertEqual(state["shown"], ["a", "b"])            # shown ids carried
        state = next_search_state(state, "data engineer", "Jakarta", False)
        self.assertEqual(state["page"], 1)                      # new query resets
        self.assertEqual(state["shown"], [])


class TestRecencySort(unittest.TestCase):
    def test_equal_scores_newest_first(self):
        from hookai.orchestrator import _sort_matches
        old = {"score": 70, "job": Job(id="1", title="a", company="", location="",
                                       description="", posted="2026-01-01")}
        new = {"score": 70, "job": Job(id="2", title="b", company="", location="",
                                       description="", posted="2026-07-01")}
        undated = {"score": 70, "job": Job(id="3", title="c", company="", location="",
                                           description="")}
        self.assertEqual([m["job"].id for m in _sort_matches([old, undated, new])],
                         ["2", "1", "3"])                        # newest first, undated last

    def test_score_still_primary(self):
        from hookai.orchestrator import _sort_matches
        low_new = {"score": 40, "job": Job(id="1", title="a", company="", location="",
                                           description="", posted="2026-07-01")}
        high_old = {"score": 90, "job": Job(id="2", title="b", company="", location="",
                                            description="", posted="2025-01-01")}
        self.assertEqual([m["job"].id for m in _sort_matches([low_new, high_old])], ["2", "1"])


class TestFallbackProvider(unittest.TestCase):
    def test_first_non_empty_wins_and_stops(self):
        calls = []

        class _P:
            def __init__(self, name, jobs):
                self.name, self.jobs = name, jobs

            def search(self, preferences, limit=10):
                calls.append(self.name)
                return self.jobs

        chain = FallbackJobDataProvider([_P("a", []), _P("b", [_job(1)]), _P("c", [_job(2)])])
        out = chain.search(None)
        self.assertEqual([j.id for j in out], ["1"])
        self.assertEqual(calls, ["a", "b"])  # c never touched — quota preserved

    def test_error_falls_through(self):
        chain = FallbackJobDataProvider([_BrokenProvider(), _ListProvider([_job(1)])])
        self.assertEqual([j.id for j in chain.search(None)], ["1"])

    def test_all_dead_returns_empty(self):
        chain = FallbackJobDataProvider([_BrokenProvider(), _ListProvider([])])
        self.assertEqual(chain.search(None), [])


class TestAdzunaDynamicCountry(unittest.TestCase):
    def test_country_name_anywhere(self):
        self.assertEqual(_resolve_adzuna_country("Berlin, Germany", "us"), "de")
        self.assertEqual(_resolve_adzuna_country("Sydney Australia", "us"), "au")
        self.assertEqual(_resolve_adzuna_country("London, United Kingdom", "us"), "gb")

    def test_code_only_as_last_comma_token(self):
        self.assertEqual(_resolve_adzuna_country("New York, US", "gb"), "us")
        self.assertEqual(_resolve_adzuna_country("London, UK", "us"), "gb")
        # bare "in"/"us" mid-string must NOT trigger (india/usa false hits)
        self.assertEqual(_resolve_adzuna_country("Remote in Europe", "gb"), "gb")

    def test_unsupported_or_empty_falls_back(self):
        self.assertEqual(_resolve_adzuna_country("Jakarta, Indonesia", "us"), "us")
        self.assertEqual(_resolve_adzuna_country("", "gb"), "gb")

    def test_search_url_uses_resolved_country(self):
        provider = AdzunaJobDataProvider(app_id="a", app_key="b", country="us")
        url = provider._build_url(Preferences(titles=["dev"], locations=["Berlin, Germany"]), 10)
        self.assertIn("/jobs/de/search/", url)


class TestProviderStack(unittest.TestCase):
    KEYS = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "ADZUNA_COUNTRY", "JOOBLE_API_KEY",
            "SEARXNG_URL", "HOOKAI_REMOTIVE")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def _set(self, **kw):
        for k in self.KEYS:
            v = kw.get(k)
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_full_fallback_chain_order(self):
        self._set(JOOBLE_API_KEY="k", ADZUNA_APP_ID="a", ADZUNA_APP_KEY="b",
                  SEARXNG_URL="http://searx", HOOKAI_REMOTIVE="0")
        provider = make_provider_from_env(llm=FakeLLM())
        self.assertIsInstance(provider, FallbackJobDataProvider)
        names = [type(p).__name__ for p in provider.providers]
        self.assertEqual(names, ["JoobleJobDataProvider", "AdzunaJobDataProvider",
                                 "WebSearchJobDataProvider"])

    def test_single_source_unwrapped(self):
        self._set(JOOBLE_API_KEY="k", HOOKAI_REMOTIVE="0")
        self.assertIsInstance(make_provider_from_env(llm=FakeLLM()), JoobleJobDataProvider)


class _PrefProvider:
    def __init__(self, jobs):
        self.jobs = jobs

    def search(self, preferences, limit=10):
        return self.jobs


class TestScoutLocationFilter(unittest.TestCase):
    JOBS = [
        Job(id="1", title="A", company="X", location="Jakarta Selatan", description=""),
        Job(id="2", title="B", company="Y", location="New York, NY", description=""),
        Job(id="3", title="C", company="Z", location="Remote (Worldwide)", description="", remote=True),
        Job(id="4", title="D", company="W", location="", description=""),  # unknown location
    ]

    def test_same_area_or_remote_only(self):
        scout = JobScout(_PrefProvider(self.JOBS))
        out = scout.discover(Preferences(titles=["x"], locations=["Jakarta"]))
        self.assertEqual([j.id for j in out], ["1", "3"])  # NY + unknown dropped

    def test_no_location_pref_keeps_all(self):
        scout = JobScout(_PrefProvider(self.JOBS))
        self.assertEqual(len(scout.discover(Preferences(titles=["x"]))), 4)

    def test_multiword_city_matches(self):
        scout = JobScout(_PrefProvider(self.JOBS))
        out = scout.discover(Preferences(titles=["x"], locations=["New York"]))
        self.assertEqual([j.id for j in out], ["2", "3"])

    def test_city_comma_country_matches_district(self):
        # "Jakarta, Indonesia" pref must keep a "Jakarta Selatan" posting —
        # tokens are punctuation-stripped so "jakarta," still matches.
        scout = JobScout(_PrefProvider(self.JOBS))
        out = scout.discover(Preferences(titles=["x"], locations=["Jakarta, Indonesia"]))
        self.assertEqual([j.id for j in out], ["1", "3"])


class TestWhereWithCountry(unittest.TestCase):
    def test_bare_home_city_gets_country(self):
        from hookai.orchestrator import _with_country
        self.assertEqual(_with_country("Jakarta", "Jakarta, Indonesia"), "Jakarta, Indonesia")
        self.assertEqual(_with_country("jakarta", "Jakarta Selatan, Indonesia"), "jakarta, Indonesia")

    def test_foreign_city_left_alone(self):
        from hookai.orchestrator import _with_country
        # Appending the home country to a foreign city would poison the search.
        self.assertEqual(_with_country("Berlin", "Jakarta, Indonesia"), "Berlin")

    def test_already_qualified_or_no_profile_country(self):
        from hookai.orchestrator import _with_country
        self.assertEqual(_with_country("Jakarta, Indonesia", "Jakarta, Indonesia"),
                         "Jakarta, Indonesia")  # comma present: as-is
        self.assertEqual(_with_country("Jakarta", "Jakarta"), "Jakarta")  # profile has no country
        self.assertEqual(_with_country("", "Jakarta, Indonesia"), "")


class TestFormFill(unittest.TestCase):
    def test_detect_ats(self):
        from hookai.tools.formfill import detect_ats
        self.assertEqual(detect_ats("https://boards.greenhouse.io/acme/jobs/401"), "greenhouse")
        self.assertEqual(detect_ats("https://job-boards.greenhouse.io/acme/jobs/401"), "greenhouse")
        self.assertEqual(detect_ats("https://jobs.lever.co/acme/1234-dev"), "lever")
        self.assertEqual(detect_ats("https://jobs.ashbyhq.com/acme/uuid"), "ashby")
        self.assertEqual(detect_ats("https://acme.com/careers/apply"), "generic")

    def test_split_name(self):
        from hookai.tools.formfill import split_name
        self.assertEqual(split_name("Jane Doe"), ("Jane", "Doe"))
        self.assertEqual(split_name("Jane van der Berg"), ("Jane", "van der Berg"))
        self.assertEqual(split_name("Prince"), ("Prince", ""))
        self.assertEqual(split_name(""), ("", ""))

    def test_build_fill_plan_maps_profile_and_package(self):
        from hookai.tools.formfill import build_fill_plan
        profile = Profile.from_dict({"identity": {
            "name": "Jane Doe", "email": "jane@x.com", "phone": "+62 811 000"}})
        package = {"cover_note": "I built APIs.",
                   "fields": {"linkedin": "https://linkedin.com/in/jane"}}
        plan = build_fill_plan(profile, package, resume_path="cv.pdf")
        by_label = {a.label_pattern: a for a in plan}
        self.assertEqual(by_label["first.?name"].value, "Jane")
        self.assertEqual(by_label["last.?name|family.?name|surname"].value, "Doe")
        self.assertEqual(by_label["e-?mail"].value, "jane@x.com")
        self.assertEqual(by_label["phone|mobile"].value, "+62 811 000")
        cover = by_label["cover.?letter|message|why.*(join|interest|apply)"]
        self.assertEqual(cover.value, "I built APIs.")
        resume = by_label["resume|\\bcv\\b|curriculum"]
        self.assertEqual(resume.kind, "file")
        self.assertEqual(resume.value, "cv.pdf")

    def test_plan_skips_empty_values(self):
        from hookai.tools.formfill import build_fill_plan
        profile = Profile.from_dict({"identity": {"name": "Jane Doe"}})  # no email/phone
        plan = build_fill_plan(profile, {}, resume_path="")
        labels = [a.label_pattern for a in plan]
        self.assertFalse(any("e-?mail" in p for p in labels))     # no email: no action
        self.assertFalse(any("resume" in p for p in labels))      # no file: no upload


class TestDocgenParse(unittest.TestCase):
    MD = ("# Jane Doe\n"
          "London | jane@x.com\n"
          "## EXPERIENCE\n"
          "**Acme Ltd** | London\n"
          "*Engineer* | Jan 2020 - Present\n"
          "- built things\n"
          "**Beta** | \n"          # empty right column must still parse as entry_head
          "*Project* | \n")

    def test_block_kinds(self):
        kinds = [b.kind for b in parse_cv_markdown(self.MD)]
        self.assertEqual(kinds, ["h1", "para", "h2", "entry_head", "entry_sub",
                                 "bullet", "entry_head", "entry_sub"])

    def test_needs_unicode_font(self):
        self.assertFalse(_needs_unicode_font("John Smith — café résumé (é, ü, ñ)"))  # Latin-1 OK
        self.assertTrue(_needs_unicode_font("陈伟 · 数据分析师"))                      # Mandarin
        self.assertTrue(_needs_unicode_font("Nguyễn Văn A"))                         # Vietnamese
        self.assertTrue(_needs_unicode_font("Иван Петров"))                          # Cyrillic

    def test_two_column_split(self):
        blocks = parse_cv_markdown(self.MD)
        head = blocks[3]
        self.assertEqual((head.text, head.right), ("Acme Ltd", "London"))
        sub = blocks[4]
        self.assertEqual((sub.text, sub.right), ("Engineer", "Jan 2020 - Present"))
        empty_right = blocks[6]
        self.assertEqual((empty_right.text, empty_right.right), ("Beta", ""))


class TestGates(unittest.TestCase):
    def test_autoblock_queues_and_blocks(self):
        gate = AutoBlockGate()
        action = OutwardAction(kind="send_email", target="x@y.com", summary="test")
        decision = gate.review(action)
        self.assertFalse(decision.approved)
        self.assertEqual(gate.pending, [action])

    def test_approve_all_is_test_only_but_approves(self):
        decision = ApproveAllGate().review(
            OutwardAction(kind="submit_application", target="acme", summary="test"))
        self.assertTrue(decision.approved)


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = SessionStore(os.path.join(self.dir.name, "s.db"), max_sessions=3)

    def tearDown(self):
        self.store.close()  # Windows can't delete an open SQLite file
        self.dir.cleanup()

    def test_roundtrip_with_profile(self):
        profile = Profile.from_dict({"identity": {"name": "Jane"}, "skills": [{"name": "SQL"}]})
        self.store.save("a", {"profile": profile, "cv_text": "cv", "jobs": [{"id": "1"}]})
        loaded = self.store.load("a")
        self.assertEqual(loaded["profile"].identity.name, "Jane")
        self.assertEqual(loaded["cv_text"], "cv")
        self.assertEqual(loaded["jobs"], [{"id": "1"}])

    def test_missing_session_is_empty(self):
        sess = self.store.load("nope")
        self.assertIsNone(sess["profile"])
        self.assertEqual(sess["jobs"], [])

    def test_history_roundtrip_and_trim(self):
        history = [{"role": "user", "text": f"m{i}"} for i in range(20)]
        self.store.save("h", {"profile": None, "cv_text": "", "jobs": [], "history": history})
        loaded = self.store.load("h")
        self.assertEqual(len(loaded["history"]), 12)          # trimmed to MAX_HISTORY
        self.assertEqual(loaded["history"][-1]["text"], "m19")  # newest kept

    def test_eviction_cap(self):
        for i in range(5):
            self.store.save(f"s{i}", {"profile": None, "cv_text": str(i), "jobs": []})
        self.assertLessEqual(self.store.count(), 3)
        self.assertEqual(self.store.load("s4")["cv_text"], "4")   # newest kept
        self.assertEqual(self.store.load("s0")["cv_text"], "")    # oldest evicted


class TestFulfillInputCap(unittest.TestCase):
    def test_oversized_cv_rejected(self):
        from hookai.heuristics import fulfill_input_error
        self.assertIsNone(fulfill_input_error("normal cv text"))
        err = fulfill_input_error("x" * (200 * 1024 + 1))
        self.assertIsNotNone(err)
        self.assertIn("large", err)

    def test_boundary_ok(self):
        from hookai.heuristics import fulfill_input_error
        self.assertIsNone(fulfill_input_error("x" * (200 * 1024)))


class TestLooksLikeCv(unittest.TestCase):
    CV = ("Jane Doe\njane@x.com | +44 7700 900123\nEXPERIENCE\nMarketing Manager at Acme "
          "2019-2024\nSkills: SEO, analytics\nEDUCATION\nBA Marketing, University of Leeds")
    QUESTION = ("Hey, I was wondering if you could explain to me in detail how the job market "
                "for data analysts is looking right now in Europe, and whether it makes sense "
                "to switch careers into it from accounting given the current economy?")

    def test_cv_paste_detected(self):
        self.assertTrue(looks_like_cv(self.CV))

    def test_long_question_not_cv(self):
        self.assertFalse(looks_like_cv(self.QUESTION))

    def test_short_text_not_cv(self):
        self.assertFalse(looks_like_cv("Jane Doe, marketing manager"))


class _NoContactProvider:
    def find_hiring_contact(self, company, role_title, posting=None):
        return None


class _HitProvider:
    def find_hiring_contact(self, company, role_title, posting=None):
        return Contact(name="Dana Reyes", title="Recruiter", email="hr@acme.com",
                       company=company, source="web")


class _DeadProvider:
    def find_hiring_contact(self, company, role_title, posting=None):
        raise RuntimeError("source down")


class TestWebSearchEnrichment(unittest.TestCase):
    CONTACT_JSON = ('{"email":"careers@acme.com","name":"Acme Talent","title":"Recruiting",'
                    '"source_url":"https://acme.com/careers"}')

    def _provider(self, results, scripted):
        class _NoNet(WebSearchEnrichmentProvider):
            def _search_web(self, query):  # skip SearXNG, feed canned results
                return results

        return _NoNet(FakeLLM(scripted=scripted), "http://searx")

    def test_extracts_public_contact_email(self):
        provider = self._provider(
            [{"title": "Careers at Acme", "url": "https://acme.com/careers",
              "content": "Email careers@acme.com to apply"}],
            {"task:extract_contact_email": self.CONTACT_JSON},
        )
        contact = provider.find_hiring_contact("Acme", "Data Analyst")
        self.assertIsNotNone(contact)
        self.assertEqual(contact.email, "careers@acme.com")
        self.assertEqual(contact.source, "web")
        self.assertEqual(contact.public_source_url, "https://acme.com/careers")

    def test_no_email_returns_none(self):
        provider = self._provider(
            [{"title": "t", "url": "http://a", "content": "c"}],
            {"task:extract_contact_email": '{"email":""}'},
        )
        self.assertIsNone(provider.find_hiring_contact("Acme", "Role"))

    def test_placeholder_email_rejected(self):
        # The LLM must not pass through obvious non-addresses (example TLDs, guesses).
        provider = self._provider(
            [{"title": "t", "url": "http://a", "content": "c"}],
            {"task:extract_contact_email": '{"email":"careers@acme.example","source_url":"http://a"}'},
        )
        self.assertIsNone(provider.find_hiring_contact("Acme", "Role"))

    def test_malformed_email_rejected(self):
        provider = self._provider(
            [{"title": "t", "url": "http://a", "content": "c"}],
            {"task:extract_contact_email": '{"email":"not-an-email","source_url":"http://a"}'},
        )
        self.assertIsNone(provider.find_hiring_contact("Acme", "Role"))

    def test_empty_search_skips_llm(self):
        class _Empty(WebSearchEnrichmentProvider):
            def _search_web(self, query):
                return []

        llm = FakeLLM()
        provider = _Empty(llm, "http://searx")
        self.assertIsNone(provider.find_hiring_contact("Acme", "Role"))
        self.assertEqual(llm.calls, [])  # no paid extraction call when there's nothing to read


class TestPostingContact(unittest.TestCase):
    """The contact is searched from the posting: use an email in the posting itself
    before falling back to a web search on the company."""

    def _web(self, results):
        class _NoNet(WebSearchEnrichmentProvider):
            def _search_web(self, query):
                return results

        return _NoNet

    def test_email_in_posting_used_directly_without_network(self):
        job = Job(id="1", title="Data Analyst", company="Acme", location="NY",
                  description="To apply, email your CV to careers@acme.com today.",
                  url="https://boards.acme.com/1")

        class _NoNet(WebSearchEnrichmentProvider):
            def _search_web(self, query):
                raise AssertionError("should not web-search when posting names a contact")

        llm = FakeLLM()
        contact = _NoNet(llm, "http://searx").find_hiring_contact("Acme", "Data Analyst", posting=job)
        self.assertEqual(contact.email, "careers@acme.com")
        self.assertEqual(contact.source, "posting")
        self.assertEqual(contact.public_source_url, "https://boards.acme.com/1")
        self.assertEqual(llm.calls, [])  # posting scan is free — no LLM/search spent

    def test_prefers_hiring_mailbox_over_generic(self):
        job = Job(id="1", title="Eng", company="Acme", location="NY",
                  description="Questions? support@acme.com. Send your CV to recruiting@acme.com.",
                  url="u")
        prov = self._web([])(FakeLLM(), "http://searx")
        self.assertEqual(prov.find_hiring_contact("Acme", "Eng", posting=job).email, "recruiting@acme.com")

    def test_placeholder_email_in_posting_ignored(self):
        job = Job(id="1", title="Eng", company="Acme", location="NY",
                  description="reach us at info@example.com", url="u")
        prov = self._web([])(FakeLLM(), "http://searx")  # no web results either
        self.assertIsNone(prov.find_hiring_contact("Acme", "Eng", posting=job))

    def test_no_email_in_posting_falls_back_to_web(self):
        job = Job(id="1", title="Eng", company="Acme", location="NY",
                  description="Great role, no contact listed here.", url="u")
        prov = self._web([{"title": "Careers", "url": "https://acme.com/careers",
                           "content": "careers@acme.com"}])(
            FakeLLM(scripted={"task:extract_contact_email":
                              '{"email":"careers@acme.com","source_url":"https://acme.com/careers"}'}),
            "http://searx")
        contact = prov.find_hiring_contact("Acme", "Eng", posting=job)
        self.assertEqual(contact.source, "web")
        self.assertEqual(contact.email, "careers@acme.com")


class TestEnrichmentSelection(unittest.TestCase):
    KEYS = ("SEARXNG_URL", "HUNTER_API_KEY", "HOOKAI_HUNTER")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def _set(self, **kw):
        for k in self.KEYS:
            v = kw.get(k)
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_web_is_default_and_hunter_excluded_even_with_key(self):
        self._set(SEARXNG_URL="http://searx", HUNTER_API_KEY="k")  # HOOKAI_HUNTER unset
        provider = make_enrichment_from_env(llm=FakeLLM())
        self.assertIsInstance(provider, WebSearchEnrichmentProvider)  # Hunter not used

    def test_hunter_is_opt_in_and_secondary(self):
        self._set(SEARXNG_URL="http://searx", HUNTER_API_KEY="k", HOOKAI_HUNTER="1")
        provider = make_enrichment_from_env(llm=FakeLLM())
        self.assertIsInstance(provider, ChainedEnrichmentProvider)
        self.assertEqual([type(p).__name__ for p in provider.providers],
                         ["WebSearchEnrichmentProvider", "HunterEnrichmentProvider"])

    def test_stub_when_no_sources(self):
        self._set()  # nothing set
        self.assertIsInstance(make_enrichment_from_env(llm=FakeLLM()), StubEnrichmentProvider)


class TestChainedEnrichment(unittest.TestCase):
    def test_returns_first_non_none(self):
        chain = ChainedEnrichmentProvider([_NoContactProvider(), _HitProvider()])
        contact = chain.find_hiring_contact("Acme", "Role")
        self.assertEqual(contact.email, "hr@acme.com")

    def test_dead_provider_fails_soft_to_next(self):
        chain = ChainedEnrichmentProvider([_DeadProvider(), _HitProvider()])
        self.assertEqual(chain.find_hiring_contact("Acme", "Role").email, "hr@acme.com")

    def test_all_miss_returns_none(self):
        chain = ChainedEnrichmentProvider([_NoContactProvider(), _DeadProvider()])
        self.assertIsNone(chain.find_hiring_contact("Acme", "Role"))

    def test_stops_at_first_hit(self):
        # A later provider must not be consulted once an earlier one answers.
        chain = ChainedEnrichmentProvider([_HitProvider(), _DeadProvider()])
        self.assertEqual(chain.find_hiring_contact("Acme", "Role").email, "hr@acme.com")


class TestProfile(unittest.TestCase):
    def test_from_dict_tolerates_extra_llm_keys(self):
        profile = Profile.from_dict({
            "identity": {"name": "J", "hallucinated_field": True},
            "skills": [{"name": "SQL", "confidence": 0.9}],
            "unknown_top_level": {},
        })
        self.assertEqual(profile.identity.name, "J")
        self.assertEqual(profile.skills[0].name, "SQL")


if __name__ == "__main__":
    unittest.main()
