import { useEffect, useRef, useState } from "react";
import { api, type ExportFormat, type AtsData } from "../api";
import { renderCvMarkdown } from "../lib/markdown";

const FORMAT_LABEL: Record<ExportFormat, string> = {
  pdf: "PDF",
  docx: "Word (.docx)",
  md: "Markdown (.md)",
};

function deriveFilename(markdown: string): string {
  const name = markdown.match(/^#\s+(.+)$/m)?.[1] || "ats-cv";
  return name.trim().replace(/\s+/g, "_").replace(/[^\w-]/g, "") || "ats-cv";
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AtsCard({ data }: { data: AtsData }) {
  const [copied, setCopied] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  const md = data.ats_cv_markdown || "";
  const stem = deriveFilename(md);
  const checklist = data.ats_checklist || [];
  const changes = data.changes || [];
  const missing = data.missing_keywords || [];

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(md);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard permission denied — non-critical, ignore
    }
  }

  async function downloadAs(format: ExportFormat) {
    setMenuOpen(false);
    setExportError("");
    if (format === "md") {
      triggerDownload(new Blob([md], { type: "text/markdown" }), `${stem}.md`);
      return;
    }
    setExporting(format);
    try {
      const blob = await api.exportCv(md, format, stem);
      triggerDownload(blob, `${stem}.${format}`);
    } catch {
      setExportError(`Couldn't generate the ${FORMAT_LABEL[format]} file. Try again.`);
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="w-full max-w-[760px] overflow-hidden rounded-2xl border border-line bg-paper shadow-sm">
      <div className="flex items-center justify-between border-b border-line bg-teal/5 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-teal/15 text-teal">✓</span>
          <span className="font-semibold text-navy">ATS-Formatted CV</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={copy}
            className="rounded-lg border border-line px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:border-teal hover:text-teal"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setMenuOpen((v) => !v)}
              disabled={exporting !== null}
              className="flex items-center gap-1.5 rounded-lg bg-navy px-2.5 py-1 text-xs font-medium text-white transition hover:bg-navy-600 disabled:opacity-60"
            >
              {exporting ? `Generating ${FORMAT_LABEL[exporting]}…` : "Download"}
              {!exporting && (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                  <path d="M6 9l6 6 6-6" />
                </svg>
              )}
            </button>
            {menuOpen && (
              <div className="absolute right-0 z-10 mt-1 w-40 overflow-hidden rounded-lg border border-line bg-paper py-1 shadow-lg">
                {(Object.keys(FORMAT_LABEL) as ExportFormat[]).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => downloadAs(fmt)}
                    className="block w-full px-3 py-2 text-left text-xs text-slate-700 hover:bg-cream"
                  >
                    {FORMAT_LABEL[fmt]}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {exportError && (
        <div className="border-b border-line bg-red-50 px-4 py-2 text-xs text-red-600">{exportError}</div>
      )}

      {checklist.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-line px-4 py-2.5">
          {checklist.map((c, i) => (
            <span
              key={i}
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] ${
                c.ok ? "bg-teal/10 text-teal" : "bg-red-50 text-red-600"
              }`}
            >
              {c.ok ? "✓" : "✕"} {c.item}
            </span>
          ))}
        </div>
      )}

      <div
        className="max-h-[440px] overflow-y-auto bg-white px-6 py-5 font-serif text-[13px] leading-relaxed text-black"
        dangerouslySetInnerHTML={{ __html: renderCvMarkdown(md) }}
      />

      {(changes.length > 0 || missing.length > 0) && (
        <div className="border-t border-line px-4 py-2.5">
          {changes.length > 0 && (
            <details>
              <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-navy">
                What changed ({changes.length})
              </summary>
              <ul className="mt-1.5 ml-4 list-disc space-y-0.5 text-xs text-slate-600">
                {changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </details>
          )}
          {missing.length > 0 && (
            <div className="mt-2 text-xs text-slate-500">
              <span className="font-medium text-slate-600">Missing keywords: </span>
              {missing.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
