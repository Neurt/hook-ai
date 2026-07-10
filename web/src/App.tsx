import { useEffect, useRef, useState } from "react";
import Header from "./components/Header";
import Message, { type Msg } from "./components/Message";
import Composer from "./components/Composer";
import { api, type AtsData, type ChatReply, type UploadReply } from "./api";
import { fileKind } from "./types";

const CHIPS = [
  "Recommend roles for me",
  "What certifications should I get?",
  "Make my CV ATS-friendly",
  "Tailor my CV to job #1",
  "Prepare my application for job #1",
];

function isAtsData(data: unknown): data is AtsData {
  return !!data && typeof data === "object" && "ats_cv_markdown" in data;
}

function toBotMessage(r: ChatReply | UploadReply): Msg {
  // Any reply carrying CV markdown (ats convert OR tailor-to-job) renders as a card.
  if ("intent" in r && isAtsData(r.data)) {
    return { role: "bot", text: r.reply, card: { kind: "ats", data: r.data } };
  }
  return { role: "bot", text: r.reply };
}

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([
    {
      role: "bot",
      text:
        "Hi! I'm **Hook AI**. Attach your CV (a PDF or a photo) with the 📎 button — or paste it " +
        "here — then ask me to **recommend roles**, **find jobs**, **prepare an application** " +
        "(you submit it), **draft an outreach email**, or **make your CV ATS-friendly**.",
    },
  ]);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Returning session (persisted id): restore the server-side transcript so the
  // visible chat matches what the bot remembers.
  useEffect(() => {
    api
      .history()
      .then((h) => {
        if (!h.history?.length) return;
        const restored: Msg[] = h.history.map((m) => ({
          role: m.role === "user" ? "user" : "bot",
          text: m.text,
        }));
        setMsgs((prev) => [
          ...prev,
          ...restored,
          { role: "bot", text: `Welcome back${h.name ? `, **${h.name.split(" ")[0]}**` : ""}. Picking up where we left off.` },
        ]);
      })
      .catch(() => {}); // no stored session: keep the greeting only
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  const push = (m: Msg) => setMsgs((prev) => [...prev, m]);
  const replaceLast = (m: Msg) =>
    setMsgs((prev) => {
      const copy = [...prev];
      copy[copy.length - 1] = m;
      return copy;
    });

  async function run(
    userMsg: Msg,
    call: () => Promise<ChatReply | UploadReply>,
    errText: string
  ) {
    if (busy) return;
    push(userMsg);
    push({ role: "bot", text: "", pending: true });
    setBusy(true);
    try {
      const r = await call();
      replaceLast(toBotMessage(r));
    } catch {
      replaceLast({ role: "bot", text: errText });
    } finally {
      setBusy(false);
    }
  }

  const send = (text: string) =>
    run(
      { role: "user", text },
      () => api.chat(text),
      "Something went wrong. Is the backend running with an API key?"
    );

  // Attach a file (optionally with a message): ingest the CV, then run the message.
  async function attach(file: File, text: string) {
    if (busy) return;
    const kind = fileKind(file);
    const previewUrl = kind === "image" ? URL.createObjectURL(file) : undefined;
    push({ role: "user", text, attachment: { name: file.name, kind, previewUrl } });
    push({ role: "bot", text: "", pending: true });
    setBusy(true);
    try {
      const up = await api.uploadCv(file);
      if (text.trim()) {
        const r = await api.chat(text);
        replaceLast(toBotMessage(r));
      } else {
        replaceLast(toBotMessage(up));
      }
    } catch {
      replaceLast({ role: "bot", text: "Couldn't read that file. Try a PDF, a photo (PNG/JPG), or a DOCX." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <Header />
      <main className="flex min-h-0 flex-1 flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
            {msgs.map((m, i) => (
              <Message key={i} m={m} />
            ))}
          </div>
        </div>
        <div className="mx-auto flex w-full max-w-3xl flex-wrap gap-2 px-4 pb-2">
          {CHIPS.map((c) => (
            <button
              key={c}
              onClick={() => send(c)}
              disabled={busy}
              className="rounded-full border border-line bg-paper px-3 py-1.5 text-[13px] text-slate-600 transition hover:border-teal hover:text-navy disabled:opacity-50"
            >
              {c}
            </button>
          ))}
        </div>
        <Composer onSend={send} onAttach={attach} disabled={busy} />
      </main>
    </div>
  );
}
