"""Extract plain text from a CV file (.txt / .md / .pdf / .docx).

PDF/DOCX need optional libraries (see requirements-extra.txt); .txt/.md need nothing.
"""
from __future__ import annotations

import os


def load_cv_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CV not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    if ext == ".pdf":
        return _load_pdf(path)
    if ext == ".docx":
        return _load_docx(path)
    raise ValueError(f"Unsupported CV format {ext!r}. Use .txt, .md, .pdf or .docx.")


def _load_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PDF support needs pypdf:  pip install pypdf") from exc
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _load_docx(path: str) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("DOCX support needs python-docx:  pip install python-docx") from exc
    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs).strip()
