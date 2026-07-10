"""Hook AI chat backend (FastAPI).

Wraps the hookai orchestrator/agents behind a small HTTP API:
  - POST /api/cv      : ingest a CV (text) -> parse into the session profile
  - POST /api/chat    : a chat turn -> routed to a capability (jobs/recommend/ats/email)
  - POST /internal/fulfill : capability call for the CROO provider (see croo-connect/)
  - GET  /api/health

Sessions are kept in memory (fine for a single-node MVP). The same capability
functions back both the chat UI and the CROO order-fulfilment path.
"""
from __future__ import annotations

import base64
import io
import os
import urllib.parse
import uuid
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from hookai.config import ConfigError, load_settings
from hookai.heuristics import fulfill_input_error, looks_like_cv, next_search_state
from hookai.llm import LLM, OpenRouterLLM
from hookai.orchestrator import Orchestrator
from hookai.profile import Profile
from hookai.store import MAX_HISTORY, SessionStore
from hookai.tools import docgen
from hookai.tools.docgen import DocgenError
from hookai.tools.enrichment import make_enrichment_from_env
from hookai.tools.job_data import Job, make_provider_from_env

app = FastAPI(title="Hook AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ── Lazy singletons ─────────────────────────────────────────────────────────
_llm: Optional[LLM] = None
_model = "?"
_orchestrator: Optional[Orchestrator] = None
# Sessions persist in SQLite (survives restarts). HOOKAI_DATA_DIR is a volume in compose.
_store = SessionStore(os.path.join(os.getenv("HOOKAI_DATA_DIR", "./data"), "sessions.db"))


def llm() -> LLM:
    global _llm, _model
    if _llm is None:
        settings = load_settings(require_key=True)  # raises ConfigError if no key
        _llm = OpenRouterLLM(settings)
        _model = settings.model
    return _llm


def orchestrator() -> Orchestrator:
    """Single shared 'brain': the same Orchestrator backs the chat and CROO paths.
    Constructed with the real job + contact providers (or their stubs, per env)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(
            llm(),
            job_provider=make_provider_from_env(llm=llm()),
            enrichment=make_enrichment_from_env(llm=llm()),
        )
    return _orchestrator


def session(sid: str) -> dict:
    return _store.load(sid)


def save_session(sid: str, sess: dict) -> None:
    _store.save(sid, sess)


# ── Capabilities: thin delegates to the single Orchestrator "brain" ─────────
def cap_parse_cv(cv_text: str, bio: str = "") -> Profile:
    return orchestrator().onboard(cv_text, bio)


def cap_recommend(profile: Profile) -> dict:
    return orchestrator().recommend(profile)


def cap_find_jobs(profile: Profile, what: str, where: str, remote: bool, limit: int = 8,
                  page: int = 1, exclude_ids: set[str] | None = None) -> list[dict]:
    return orchestrator().find_jobs(profile, what, where, remote, limit,
                                    page=page, exclude_ids=exclude_ids)


def cap_to_ats(cv_text: str) -> dict:
    return orchestrator().to_ats(cv_text)


def cap_draft_email(profile: Profile, job: Job) -> dict:
    return orchestrator().draft_email(profile, job)


def cap_prepare_application(profile: Profile, job: Job) -> dict:
    return orchestrator().prepare_application(profile, job)


def cap_recommend_certs(profile: Profile) -> dict:
    return orchestrator().recommend_certifications(profile)


def cap_tailor(profile: Profile, job: Job) -> dict:
    return orchestrator().tailor_for(profile, job)


# ── Request models ──────────────────────────────────────────────────────────
class CvIn(BaseModel):
    session_id: str = ""
    cv_text: str


class ChatIn(BaseModel):
    session_id: str = ""
    message: str


class FulfillIn(BaseModel):
    task: str               # "recommend" | "find_jobs" | "ats" | "draft_email"
    cv_text: str
    params: dict[str, Any] = {}


class ExportIn(BaseModel):
    markdown: str
    format: str = "docx"    # "docx" | "pdf" | "md"
    filename: str = "ats-cv"


_EXPORT_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "md": "text/markdown",
}


# ── Routes ──────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health() -> dict:
    ok = True
    detail = "ready"
    model = _model
    try:
        model = load_settings(require_key=True).model
    except ConfigError as exc:
        ok, detail = False, str(exc)
    return {"ok": ok, "model": model, "detail": detail}


@app.get("/api/session/{sid}/history")
def session_history(sid: str) -> dict:
    """Restore a returning browser's chat transcript (session id is client-held)."""
    sess = session(sid)
    profile = sess.get("profile")
    return {"session_id": sid,
            "name": profile.identity.name if profile else "",
            "history": sess.get("history") or []}


@app.post("/api/export")
def export_cv(body: ExportIn) -> Response:
    """Render ATS CV markdown to a downloadable file. Stateless — the client already
    has the markdown (from an earlier /api/chat "ats" reply); we just convert format."""
    fmt = (body.format or "").lower()
    if fmt not in _EXPORT_MEDIA_TYPES:
        raise HTTPException(400, f"Unsupported format {fmt!r}. Use docx, pdf, or md.")
    if not body.markdown.strip():
        raise HTTPException(400, "No CV content to export.")

    stem = "".join(c for c in (body.filename or "ats-cv") if c.isalnum() or c in "-_") or "ats-cv"
    try:
        if fmt == "docx":
            data = docgen.render_docx_bytes(body.markdown)
        elif fmt == "pdf":
            data = docgen.render_pdf_bytes(body.markdown)
        else:
            data = body.markdown.encode("utf-8")
    except DocgenError as exc:
        raise HTTPException(500, str(exc)) from exc

    return Response(
        content=data,
        media_type=_EXPORT_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'},
    )


@app.post("/api/cv")
def ingest_cv(body: CvIn) -> dict:
    sid = body.session_id or str(uuid.uuid4())
    profile = cap_parse_cv(body.cv_text)
    sess = session(sid)
    sess["profile"] = profile
    sess["cv_text"] = body.cv_text
    save_session(sid, sess)
    return {"session_id": sid, "name": profile.identity.name,
            "skills": [s.name for s in profile.skills],
            "reply": f"Got your CV, {profile.identity.name or 'there'}. Ask me to "
                     "**recommend roles**, **find jobs**, **draft an email**, or "
                     "**make your CV ATS-friendly**."}


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages).strip()


def _extract_docx(data: bytes) -> str:
    import docx  # python-docx

    return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs).strip()


@app.post("/api/cv/upload")
async def upload_cv(session_id: str = Form(""), file: UploadFile = File(...)) -> dict:
    sid = session_id or str(uuid.uuid4())
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:  # server-side cap (nginx allows 25M; keep headroom)
        return {"session_id": sid, "reply": "That file is over 15 MB. Export a smaller "
                "PDF or a compressed photo and try again."}
    fname = (file.filename or "").lower()
    ctype = file.content_type or ""
    try:
        if fname.endswith(".pdf") or ctype == "application/pdf":
            text = _extract_pdf(raw)
            if not text:
                return {"session_id": sid, "reply": "I couldn't read text from that PDF — it looks "
                        "scanned/image-only. Try a text-based PDF, or attach a **photo** of it instead."}
        elif fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or ctype.startswith("image/"):
            mime = ctype if ctype.startswith("image/") else (
                "image/png" if fname.endswith(".png") else "image/jpeg")
            b64 = base64.b64encode(raw).decode()
            text = llm().read_image(
                f"data:{mime};base64,{b64}",
                "Transcribe this CV/resume image to plain text. Preserve every section, role, date, "
                "bullet and skill. Output only the transcribed text.",
            )
        elif fname.endswith(".docx"):
            text = _extract_docx(raw)
        else:
            return {"session_id": sid, "reply": "Unsupported file. Attach a **PDF**, a **photo** "
                    "(PNG/JPG), or a DOCX."}
    except Exception as exc:  # noqa: BLE001 - surface a friendly message
        return {"session_id": sid, "reply": f"Sorry, I couldn't read that file ({exc})."}

    if not text.strip():
        return {"session_id": sid, "reply": "I couldn't extract any text from that file."}

    profile = cap_parse_cv(text)
    sess = session(sid)
    sess["profile"] = profile
    sess["cv_text"] = text
    fallback = (f"Got your CV, {profile.identity.name or 'there'} — read from **{file.filename}**. "
                "Ask me to **recommend roles**, **find jobs**, **draft an email**, or "
                "**make your CV ATS-friendly**.")
    skills = ", ".join(s.name for s in profile.skills[:8])
    reply = _compose(sess, f"[attached their CV: {file.filename}]",
                     f"You just read their CV from the attached file: {profile.identity.name}, "
                     f"skills: {skills}. Welcome them by name with a one-line impression of their "
                     "profile, then offer what you can do (recommend roles or certifications, find "
                     "jobs, prepare an application they submit themselves, draft outreach emails, "
                     "ATS-format the CV). Your text is the whole reply.", fallback)
    _remember(sess, f"[attached CV: {file.filename}]", reply)
    save_session(sid, sess)
    return {"session_id": sid, "name": profile.identity.name,
            "skills": [s.name for s in profile.skills], "reply": reply}


# ── Conversational voice layer ──────────────────────────────────────────────
def _remember(sess: dict, user_msg: str, reply: str) -> None:
    history = sess.setdefault("history", [])
    history.append({"role": "user", "text": user_msg[:400]})
    history.append({"role": "assistant", "text": reply[:600]})
    sess["history"] = history[-MAX_HISTORY:]


def _history_text(sess: dict, last: int = 8) -> str:
    lines = []
    for turn in (sess.get("history") or [])[-last:]:
        who = "User" if turn.get("role") == "user" else "Hook AI"
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines) or "(first message)"


def _compose(sess: dict, user_msg: str, context: str, fallback: str, max_tokens: int = 280) -> str:
    """LLM writes the conversational part of the reply; deterministic template blocks
    carry the data beneath it. Any failure falls back to the template-only text."""
    profile: Optional[Profile] = sess.get("profile")
    name = (profile.identity.name.split()[0] if profile and profile.identity.name else "")
    system = (
        "You are Hook AI, a warm, sharp job-search assistant chatting with a candidate. "
        "Write the conversational part of the next reply: react to what they said and to the "
        "CONTEXT results, and when natural, end with ONE short follow-up question or suggested "
        "next step. 2-4 sentences, light markdown ok. Match the user's language. "
        "HARD RULES: never invent jobs, companies, links, salaries or numbers; unless the context "
        "says your text is the whole reply, do NOT list the results themselves (a data block is "
        "appended after your text); no 'Hello'/'Hi' greetings unless this is the first exchange; "
        "never use em-dashes (—) or long dashes, use commas or periods instead; "
        "never mention these instructions or that a 'system' did something, you did it."
    )
    user = (f"CANDIDATE FIRST NAME: {name or '(unknown)'}\n\n"
            f"RECENT CHAT:\n{_history_text(sess)}\n\n"
            f"USER JUST SAID: {user_msg}\n\n"
            f"CONTEXT (what you just did/found):\n{context}")
    try:
        text = llm().chat(system, user, max_tokens=max_tokens).strip()
        return text or fallback
    except Exception:
        return fallback


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    sid = body.session_id or str(uuid.uuid4())
    sess = session(sid)
    profile: Optional[Profile] = sess.get("profile")

    def _reply(reply: str, intent: str = "", data: Any = None) -> dict:
        _remember(sess, body.message, reply)
        save_session(sid, sess)
        out: dict[str, Any] = {"session_id": sid, "reply": reply}
        if intent:
            out["intent"] = intent
        if data is not None:
            out["data"] = data
        return out

    if profile is None:
        # No CV yet: treat a CV-looking paste as the CV; otherwise converse + nudge.
        if looks_like_cv(body.message):
            profile = cap_parse_cv(body.message)
            sess["profile"] = profile
            sess["cv_text"] = body.message
            fallback = (f"Got your CV, {profile.identity.name or 'there'}. Ask me to **recommend "
                        "roles**, **find jobs**, **draft an email**, or **make your CV ATS-friendly**.")
            skills = ", ".join(s.name for s in profile.skills[:8])
            return _reply(_compose(sess, body.message,
                          f"You just read their CV: {profile.identity.name}, skills: {skills}. "
                          "Welcome them by name with a one-line impression of their profile, then "
                          "offer what you can do (recommend roles or certifications, find jobs, "
                          "prepare an application they submit themselves, draft outreach emails, "
                          "ATS-format the CV). Your text is the whole reply.", fallback))
        fallback = ("I need your CV first — attach it with the 📎 button (PDF or a photo), or "
                    "paste the full text here.")
        return _reply(_compose(sess, body.message,
                      "They haven't shared a CV yet, so you can't do any real work. Respond to "
                      "their message naturally, then ask them to attach (📎) or paste their CV. "
                      "Your ONLY abilities (never claim others): recommend roles, recommend "
                      "certifications, find live jobs, tailor their CV to a specific job, prepare "
                      "applications (they submit), draft outreach emails, ATS-format their CV. "
                      "Your text is the whole reply.", fallback))

    route = _route(body.message)
    intent = route.get("intent", "help")

    if intent == "recommend":
        data = cap_recommend(profile)
        roles = ", ".join(r.get("title", "") for r in data.get("roles", [])[:4])
        gaps = len(data.get("skills", {}).get("gaps", []))
        lead = _compose(sess, body.message,
                        f"You analysed their CV and recommend these target roles: {roles or 'none'}; "
                        f"plus {gaps} skill gaps to work on. The full list follows your text.", "")
        return _reply(f"{lead}\n\n{_fmt_recommend(data)}".strip(), intent, data)

    if intent == "certs":
        data = cap_recommend_certs(profile)
        names = ", ".join(c.get("name", "") for c in data.get("certifications", [])[:4])
        lead = _compose(sess, body.message,
                        f"You picked real industry certifications for them: {names}. "
                        "The detailed list follows your text.", "")
        return _reply(f"{lead}\n\n{_fmt_certs(data)}".strip(), intent, data)

    if intent == "find_jobs":
        what, where = route.get("what", ""), route.get("where", "")
        remote = bool(route.get("remote", False))
        # Same query again ("find more jobs") pages forward and skips shown ids.
        search = next_search_state(sess.get("search") or {}, what, where, remote)
        jobs = cap_find_jobs(profile, what, where, remote,
                             page=search["page"], exclude_ids=set(search["shown"]))
        if not jobs and search["page"] > 1:  # source ran dry: wrap to page 1 minus shown
            search["page"] = 1
            jobs = cap_find_jobs(profile, what, where, remote,
                                 page=1, exclude_ids=set(search["shown"]))
        search["shown"] = (search["shown"] + [j["id"] for j in jobs])[-200:]
        sess["search"] = search
        sess["jobs"] = jobs
        top = "; ".join(f"{j['title']} @ {j['company']} (score {j['score']})" for j in jobs[:3])
        context = (f"You searched live job boards (what={route.get('what') or 'from their CV'}, "
                   f"where={route.get('where') or 'their location'}): {len(jobs)} good matches. "
                   + (f"Top: {top}. The numbered list follows your text; they can say 'apply to #N' "
                      f"or 'draft an email to #N'." if jobs else
                      "None matched their profile well — suggest naming a role or another location. "
                      "Your text is the whole reply."))
        lead = _compose(sess, body.message, context, "")
        return _reply(f"{lead}\n\n{_fmt_jobs(jobs)}".strip(), intent, {"jobs": jobs})

    if intent == "ats":
        data = cap_to_ats(sess.get("cv_text", ""))
        changes = data.get("changes") or []
        lead = _compose(sess, body.message,
                        f"You converted their CV to ATS format with {len(changes)} changes "
                        f"(e.g. {'; '.join(changes[:2]) or 'none needed'}). A rendered CV card with "
                        "Copy/Download buttons appears below your text — don't describe its contents.",
                        _fmt_ats(data))
        return _reply(lead, intent, data)

    if intent == "tailor":
        job = _pick_job(sess, route)
        if job is None:
            fallback = ("Find some jobs first (e.g. \"find data analyst jobs in London\"), "
                        "then say \"tailor my CV to #1\".")
            return _reply(_compose(sess, body.message,
                          "They want their CV tailored to a job but there's no job list in this "
                          "session yet. Tell them to search jobs first, then say 'tailor my CV to "
                          "#N'. Your text is the whole reply.", fallback))
        data = cap_tailor(profile, job)
        missing = ", ".join(data.get("missing_keywords", [])[:6])
        lead = _compose(sess, body.message,
                        f"You tailored their CV to {job.title} @ {job.company}: fit score "
                        f"{data.get('fit_score', '?')}/100. "
                        + (f"Keywords they lack: {missing}. " if missing else "")
                        + "A rendered CV card with Copy/Download buttons appears below your text — "
                        "don't describe its contents.",
                        f"Tailored your CV to **{job.title} @ {job.company}** "
                        f"(fit {data.get('fit_score', '?')}/100).")
        return _reply(lead, intent, data)

    if intent == "draft_email":
        job = _pick_job(sess, route)
        if job is None:
            fallback = ("Find some jobs first (e.g. \"find data analyst jobs in London\"), "
                        "then say \"draft an email to #1\".")
            return _reply(_compose(sess, body.message,
                          "They want an outreach email but there's no job list in this session yet. "
                          "Tell them to search jobs first, e.g. 'find data analyst jobs in London', "
                          "then reference one by number. Your text is the whole reply.", fallback))
        data = cap_draft_email(profile, job)
        src = "a real address found via Hunter.io" if data.get("source") == "hunter" else "a placeholder address"
        lead = _compose(sess, body.message,
                        f"You drafted an outreach email for {job.title} @ {job.company}, addressed to "
                        f"{src}. The draft follows your text — remind them to review before sending.", "")
        return _reply(f"{lead}\n\n{_fmt_email(job, data)}".strip(), intent, data)

    if intent == "apply":
        job = _pick_job(sess, route)
        if job is None:
            fallback = ("Find some jobs first (e.g. \"find data analyst jobs in London\"), "
                        "then say \"apply to #1\" and I'll prepare the application for you to submit.")
            return _reply(_compose(sess, body.message,
                          "They want to apply but there's no job list in this session yet. Tell them "
                          "to search jobs first, then say 'apply to #N'. Your text is the whole reply.",
                          fallback))
        data = cap_prepare_application(profile, job)
        lead = _compose(sess, body.message,
                        f"You prepared their application for {job.title} @ {job.company}: pre-filled "
                        "details, screening answers and a cover note follow your text. You never "
                        "submit for them — they review and submit at the link.", "")
        return _reply(f"{lead}\n\n{_fmt_application(data)}".strip(), intent, data)

    # help / smalltalk / anything else: fully conversational, with history.
    fallback = ("I can **recommend roles**, **recommend certifications**, **find jobs**, "
                "**tailor your CV to a job** (\"tailor my CV to #1\"), **prepare an application** "
                "(you submit it), **draft an outreach email**, or **convert your CV to ATS "
                "format**. What would you like?")
    return _reply(_compose(sess, body.message,
                  "No tool ran for this message. Your abilities: recommend roles, recommend real "
                  "certifications, find live jobs (multiple sources), tailor their CV to a specific "
                  "job from the list, prepare a job application (they submit it themselves), draft "
                  "outreach emails to hiring contacts, convert their CV to ATS format with PDF/DOCX "
                  "download. Answer their message directly and helpfully — only pitch abilities if "
                  "relevant. Your text is the whole reply.", fallback), intent)


@app.post("/internal/fulfill")
def fulfill(body: FulfillIn) -> dict:
    """Capability entry point for the CROO provider (croo-connect/). Stateless.
    Returns {"deliverable": ...} or {"error": ...} — never a 500 for bad input,
    so a paid order settles with a clean error instead of hanging."""
    input_error = fulfill_input_error(body.cv_text)
    if input_error:
        return {"error": input_error}
    if not isinstance(body.params, dict):
        body.params = {}
    task = body.task
    if task == "ats":
        # ats works on the raw CV text — parsing a Profile here would be a wasted LLM call.
        return {"deliverable": cap_to_ats(body.cv_text)}
    profile = cap_parse_cv(body.cv_text, body.params.get("bio", ""))
    if task == "recommend":
        return {"deliverable": cap_recommend(profile)}
    if task == "certs":
        return {"deliverable": cap_recommend_certs(profile)}
    if task == "find_jobs":
        return {"deliverable": cap_find_jobs(profile, body.params.get("what", ""),
                body.params.get("where", ""), bool(body.params.get("remote", False)))}
    if task in ("draft_email", "prepare_application", "tailor"):
        p = body.params
        job = Job(id="croo", title=p.get("title", ""), company=p.get("company", ""),
                  location=p.get("where", ""), description=p.get("description", ""),
                  url=p.get("url", ""))
        if task == "draft_email":
            return {"deliverable": cap_draft_email(profile, job)}
        if task == "tailor":
            return {"deliverable": cap_tailor(profile, job)}
        return {"deliverable": cap_prepare_application(profile, job)}
    return {"error": f"unknown task {task!r}"}


# ── Intent routing + formatting ─────────────────────────────────────────────
def _route(message: str) -> dict:
    system = (
        "Route a job-seeker's chat message to ONE capability. Return ONLY JSON: "
        '{"intent":"find_jobs|recommend|certs|ats|tailor|draft_email|apply|help","what":"","where":"",'
        '"remote":false,"pick":null}. '
        "find_jobs=wants job listings. what = a concrete job title/field ONLY (e.g. 'actuarial "
        "analyst', 'data engineer') — NEVER filler like 'jobs', 'real jobs', 'work', 'a job'; if "
        "the message names no title/field, leave what empty (the profile fills it in). where = a "
        "city/region if named; a bare country like 'US' also goes in where. "
        "recommend=wants role suggestions or skills to improve. "
        "certs=wants certification/license/credential/exam recommendations. "
        "ats=wants CV checked/converted for ATS (no specific job). "
        "tailor=wants their CV tailored/customized/adapted to ONE specific job "
        "(pick=referenced job number). "
        "draft_email=wants an outreach/HR email (pick=referenced job number). "
        "apply=wants to apply to / prepare an application for a job (pick=referenced job number). "
        "help=greeting or anything else."
    )
    try:
        data = llm().complete_json(system, message, max_tokens=200)
        return data if isinstance(data, dict) else {"intent": "help"}
    except Exception:
        return {"intent": "help"}


def _pick_job(sess: dict, route: dict) -> Optional[Job]:
    jobs = sess.get("jobs") or []
    if not jobs:
        return None
    idx = route.get("pick")
    i = (int(idx) - 1) if isinstance(idx, (int, str)) and str(idx).isdigit() else 0
    i = max(0, min(i, len(jobs) - 1))
    j = jobs[i]
    return Job(id=j["id"], title=j["title"], company=j["company"], location=j["location"],
              description=j.get("reason", ""), url=j.get("url", ""))


def _fmt_recommend(data: dict) -> str:
    lines = ["**Recommended roles**"]
    for r in data.get("roles", []):
        lines.append(f"- **{r.get('title','?')}** ({r.get('seniority','')}): {r.get('why','')}")
    gaps = data.get("skills", {}).get("gaps", [])
    if gaps:
        lines.append("\n**Skills to improve**")
        for g in gaps:
            lines.append(f"- [{g.get('priority','')}] {g.get('skill','')}: {g.get('why','')}")
    return "\n".join(lines)


def _fmt_jobs(jobs: list[dict]) -> str:
    if not jobs:
        return ("I found listings, but none were a genuine match for your profile. Try naming a "
                "role (e.g. \"find actuarial analyst jobs in New York\") or a different location.")
    lines = ["**Top matches** (say \"draft an email to #1\" to reach out, or \"apply to #1\"):"]
    for n, j in enumerate(jobs, 1):
        sal = f" · {j['salary']}" if j.get("salary") else ""
        src = f" · via {j['source']}" if j.get("source") else ""
        lines.append(f"{n}. [{j['score']}] **{j['title']}** @ {j['company']} · {j['location']}{sal}{src}")
        if j.get("url"):
            lines.append(f"   {j['url']}")
    return "\n".join(lines)


def _fmt_ats(data: dict) -> str:
    if not data.get("ats_cv_markdown", "").strip():
        return "I couldn't generate an ATS version of your CV. Try again."
    n = len(data.get("changes") or [])
    if not n:
        return "Your CV already looks ATS-friendly. Here it is below."
    return f"Here's your ATS-formatted CV with {n} change{'s' if n != 1 else ''} applied."


def _fmt_certs(data: dict) -> str:
    certs = data.get("certifications") or []
    if not certs:
        return "I couldn't produce certification recommendations. Try again."
    lines = ["**Recommended certifications** (industry-recognized, in the order I'd pursue them):"]
    for i, c in enumerate(certs, 1):
        lines.append(f"{i}. **{c.get('name','?')}** · {c.get('issuer','')} · {c.get('level','')}")
        if c.get("why"):
            lines.append(f"   {c['why']}")
        if c.get("typical_path"):
            lines.append(f"   *Path:* {c['typical_path']}")
        site = (c.get("official_site") or "").removeprefix("https://").removeprefix("http://").strip("/")
        if site:
            lines.append(f"   Official: https://{site}")
    lines.append("\n_Exam fees, prerequisites and renewal rules change. Confirm on the issuer's official site._")
    return "\n".join(lines)


def _fmt_application(data: dict) -> str:
    job = data.get("job", {})
    lines = [f"**Application prepared: {job.get('title','?')} @ {job.get('company','?')}**",
             "", "**Your details (pre-filled):**"]
    for key, value in (data.get("fields") or {}).items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    answers = data.get("screening_answers") or []
    if answers:
        lines.append("\n**Suggested screening answers:**")
        for a in answers:
            lines.append(f"- *{a.get('question','')}* — {a.get('answer','')}")
    note = (data.get("cover_note") or "").strip()
    if note:
        lines.append(f"\n**Cover note:**\n{note}")
    lines.append("\n⚠️ **I don't submit applications for you.** Review the above, then apply here:")
    lines.append(job.get("url") or "(no application link available for this posting)")
    return "\n".join(lines)


def _fmt_email(job: Job, data: dict) -> str:
    src = {"hunter": "found via Hunter.io", "posting": "listed in the job posting",
           "web": "found on the company's public pages, verify before sending",
           }.get(data.get("source", ""), "PLACEHOLDER, find the real address before sending")
    to, subject, body = data.get("to", ""), data.get("subject", ""), data.get("body", "")
    out = (f"**Draft email: {job.title} @ {job.company}**\n\n"
           f"To: {to}  _({src})_\n"
           f"Subject: {subject}\n\n{body}")
    # One-click handoff to the user's own mail app (they send from their own
    # account — we never send). No link for the stub's fake address.
    if data.get("source") in ("hunter", "posting", "web") and to:
        params = urllib.parse.urlencode({"subject": subject, "body": body},
                                        quote_via=urllib.parse.quote)
        out += f"\n\n[**Open in your email app**](mailto:{to}?{params})"
    return out
