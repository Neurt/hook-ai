import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { fileKind } from "../types";

const KIND_LABEL = { image: "IMG", pdf: "PDF", doc: "DOC" } as const;

export default function Composer({
  onSend,
  onAttach,
  disabled,
}: {
  onSend: (text: string) => void;
  onAttach: (file: File, text: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = value.trim();
    if (file) {
      onAttach(file, text);
      setFile(null);
      setValue("");
    } else if (text) {
      onSend(text);
      setValue("");
    }
  }

  function onFile(e: ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0];
    if (picked) setFile(picked);
    e.target.value = "";
  }

  const nothingToSend = !file && !value.trim();

  return (
    <form onSubmit={submit} className="border-t border-line bg-paper">
      <div className="mx-auto max-w-3xl px-4 py-3">
        {file && (
          <div className="mb-2 flex">
            <div className="inline-flex items-center gap-2 rounded-lg border border-line bg-cream px-2.5 py-1.5">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded bg-navy/10 text-[9px] font-bold text-navy">
                {KIND_LABEL[fileKind(file)]}
              </span>
              <span className="max-w-[240px] truncate text-sm text-slate-700">{file.name}</span>
              <button
                type="button"
                onClick={() => setFile(null)}
                title="Remove attachment"
                className="ml-1 grid h-5 w-5 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-red-500"
              >
                ✕
              </button>
            </div>
          </div>
        )}
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            hidden
            accept=".pdf,.png,.jpg,.jpeg,.webp,.docx,image/*"
            onChange={onFile}
          />
          <button
            type="button"
            title="Attach CV (PDF or photo)"
            disabled={disabled}
            onClick={() => fileRef.current?.click()}
            className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-line text-navy transition hover:border-teal hover:text-teal disabled:opacity-50"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={file ? "Add a message (optional)…" : "Message Hook AI…  (or paste your CV)"}
            className="flex-1 rounded-xl border border-line bg-cream px-4 py-3 text-slate-800 outline-none placeholder:text-slate-400 focus:border-navy"
          />
          <button
            type="submit"
            disabled={disabled || nothingToSend}
            className="rounded-xl bg-navy px-5 py-3 font-semibold text-white transition hover:bg-navy-600 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </form>
  );
}
