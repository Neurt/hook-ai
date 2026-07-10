"""Offline structural smoke test — NO API key, NO network, NO deps beyond stdlib.

Exercises the whole pipeline with a FakeLLM (canned JSON keyed by task tag) and
asserts the wiring holds and the approval gate blocks both outward actions.

    python smoke_test.py
"""
from __future__ import annotations

from hookai.agents import (
    ApplicationAssistant,
    CVTailor,
    MatchRank,
    Outreach,
    SkillsAdvisor,
)
from hookai.gates import AutoBlockGate
from hookai.llm import FakeLLM
from hookai.orchestrator import Orchestrator

SCRIPTED = {
    CVTailor.TAG_PARSE: (
        '{"identity":{"name":"Jane Tester","location":"Remote"},'
        '"skills":[{"name":"Python"}],'
        '"preferences":{"titles":["engineer"],"remote":true}}'
    ),
    MatchRank.TAG: '{"matches":[{"id":"j1","score":88,"reason":"strong python match"}]}',
    CVTailor.TAG_TAILOR: (
        '{"ats_cv_markdown":"# Jane Tester","fit_score":80,'
        '"rationale":"good overlap","missing_keywords":["kubernetes"]}'
    ),
    ApplicationAssistant.TAG: (
        '{"fields":{"full_name":"Jane Tester"},"screening_answers":[],"cover_note":"hi"}'
    ),
    Outreach.TAG: (
        '{"subject":"Re: Backend role","body":"Hello.\\n[[SENDER_NAME]]\\n[[UNSUBSCRIBE]]\\n[[POSTAL_ADDRESS]]"}'
    ),
    SkillsAdvisor.TAG: (
        '{"gaps":[{"skill":"Kubernetes","why":"common requirement","priority":"high"}],"plan":[]}'
    ),
}


def main() -> None:
    orch = Orchestrator(FakeLLM(scripted=SCRIPTED), gate=AutoBlockGate())

    profile = orch.onboard("raw cv text", "bio")
    assert profile.identity.name == "Jane Tester", profile.identity.name

    jobs, matches = orch.find_matches(profile)
    assert jobs, "stub provider returned no jobs"
    assert matches and matches[0]["job"].id == "j1", "ranking did not resolve job id"

    top = matches[0]["job"]
    tailored = orch.tailor_for(profile, top)
    assert tailored["fit_score"] == 80

    _, apply_res = orch.assist_apply(profile, top, tailored["ats_cv_markdown"])
    assert apply_res["status"] == "pending_approval", "gate must block application submit"

    contact, _, send_res = orch.reach_out(profile, top)
    assert send_res["status"] == "pending_approval", "gate must block email send"
    assert send_res["compliance_issues"] == [], f"unexpected: {send_res['compliance_issues']}"

    advice = orch.advise_skills(profile, jobs)
    assert advice["gaps"][0]["skill"] == "Kubernetes"

    assert len(orch.gate.pending) == 2, "exactly two outward actions should be pending"

    print("OK — pipeline wired; 6 specialists ran; gate blocked 2 outward actions.")
    print("    pending:", [a.kind for a in orch.gate.pending])


if __name__ == "__main__":
    main()
