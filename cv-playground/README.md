# cv-playground

Drop in a CV, get **LLM job recommendations + skills to improve** — run against your
real CV through OpenRouter. A focused test of two Hook AI pillars; reuses the
`hookai` package in [../app/](../app/).

## How it works

1. **Reads your CV** (`.txt` / `.md` / `.pdf` / `.docx`).
2. **ATS gate** — checks if the CV is ATS-friendly. If not, it converts to ATS format
   and shows you the result to **review/adjust**: Enter to accept, type changes to
   refine, or `e` to edit the saved file. (Skip with `--no-ats`.)
3. **Prompts you for preferences** — inline suggestions (roles/location/seniority from
   your CV); Enter to accept or type your own. (Or pass `--bio` to skip.)
4. **Recommends roles** — 3–5 grounded in your CV + preferences.
5. **Skill gaps** — what to improve against those roles, with priorities + actions.

## Use it

```powershell
# from this folder
cd "Hook AI\cv-playground"

# deps: openai + python-dotenv (from ../app), plus parsers for pdf/docx
pip install -r ..\app\requirements.txt
pip install -r requirements-extra.txt        # only if you use PDF/DOCX

# your OpenRouter key must be in ..\app\.env  (OPENROUTER_API_KEY=...)

# interactive — ATS gate (convert + review if needed), then preferences prompt:
python recommend.py cvs\my_cv.pdf

# variations:
python recommend.py cvs\my_cv.pdf --bio "remote senior data roles, EU hours"  # skip bio prompt
python recommend.py cvs\my_cv.pdf --no-ats           # skip the ATS check/convert
python recommend.py cvs\my_cv.pdf --no-interactive   # no prompts at all
python recommend.py cvs\my_cv.docx --json            # raw JSON (non-interactive)
```

Put your own CV in `cvs/` — that folder is git-ignored (CVs are personal data), so
nothing real gets committed; only `sample_cv.txt` is tracked.

## Convert a CV to ATS format

`to_ats.py` reformats a non-ATS CV into a clean, ATS-friendly version — **preserving
all real content** (no inventing, no dropping) — and saves it to `out/`.

```powershell
python to_ats.py cvs\messy_cv.txt                 # try the bundled messy sample
python to_ats.py cvs\my_cv.pdf --docx             # also write an uploadable .docx
python to_ats.py cvs\my_cv.pdf --job cvs\jd.txt   # align keywords to a job
```

You get the reformatted CV (markdown) + a list of what changed + an ATS checklist.
The `.md` is the editable source; `--docx` produces a single-column Word file to
upload. ("ATS-friendly" = structure: single column, standard headings, no
tables/columns/images — both outputs keep that.)

## Find real jobs (Adzuna)

`find_jobs.py` searches **live postings** via the Adzuna API and ranks them against
your CV. It needs free Adzuna credentials:

1. Register an app at https://developer.adzuna.com/ (free) → get an **app_id** + **app_key**.
2. Add them to `..\app\.env`:
   ```
   ADZUNA_APP_ID=your_app_id
   ADZUNA_APP_KEY=your_app_key
   ADZUNA_COUNTRY=gb        # gb, us, au, ca, de, fr, in, sg, ... ( --where must match )
   ```
3. Run:
   ```powershell
   python find_jobs.py cvs\sample_cv.txt --what "data analyst" --where London
   python find_jobs.py cvs\my_cv.pdf --remote --top 8
   ```

Without Adzuna keys it falls back to stub sample jobs, so you can still see the ranking flow.

## Draft an email to a hiring contact

`draft_email.py` picks a job (live from Adzuna, or `--company`/`--title`) and drafts a
personalized 1:1 email from your CV, signed with your details. **Nothing is sent.**

```powershell
python draft_email.py cvs\my_cv.pdf --what "data analyst" --where London          # pick interactively
python draft_email.py cvs\my_cv.pdf --what "data analyst" --where London --pick 1  # pick the 1st job
python draft_email.py cvs\my_cv.pdf --company "Acme Ltd" --title "Data Analyst"    # skip the search
```

The hiring contact uses **Hunter.io** when you set `HUNTER_API_KEY` in `..\app\.env`
(free key at https://hunter.io/api-keys — finds public HR/recruiting emails and records
the source URL for provenance). Without a key it falls back to a placeholder address.
Actually *sending* still needs a provider in `app/hookai/tools/email.py`. Drafts → `out/`.

## Notes
- `recommend.py` generates role recommendations **from your CV** (no job board needed);
  `find_jobs.py` ranks **real Adzuna postings**. Use whichever fits.
- Scanned/image-only PDFs won't extract text — export a text-based PDF or use `.docx`/`.txt`.
- Model is whatever `OPENROUTER_MODEL` is set to in `../app/.env`.
