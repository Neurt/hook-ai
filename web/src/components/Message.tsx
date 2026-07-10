import type { AtsData } from "../api";
import type { AttachmentInfo } from "../types";
import { renderMarkdown } from "../lib/markdown";
import AttachmentChip from "./AttachmentChip";
import AtsCard from "./AtsCard";

export interface Msg {
  role: "user" | "bot";
  text: string;
  pending?: boolean;
  attachment?: AttachmentInfo;
  card?: { kind: "ats"; data: AtsData };
}

export default function Message({ m }: { m: Msg }) {
  const isUser = m.role === "user";

  if (m.card?.kind === "ats") {
    return (
      <div className="flex flex-col items-start gap-2">
        {m.text && (
          <div className="max-w-[760px] rounded-2xl rounded-tl-sm border border-line bg-paper px-4 py-3 text-slate-800 shadow-sm">
            <span dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }} />
          </div>
        )}
        <AtsCard data={m.card.data} />
      </div>
    );
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[760px] rounded-2xl px-4 py-3 leading-relaxed shadow-sm ${
          isUser
            ? "rounded-tr-sm bg-navy text-white"
            : "rounded-tl-sm border border-line bg-paper text-slate-800"
        }`}
      >
        {m.attachment ? (
          <div className="space-y-2">
            <AttachmentChip {...m.attachment} />
            {m.text && <span className="block whitespace-pre-wrap">{m.text}</span>}
          </div>
        ) : m.pending ? (
          <span className="inline-flex gap-1 text-slate-400">
            <span className="animate-bounce">•</span>
            <span className="animate-bounce [animation-delay:120ms]">•</span>
            <span className="animate-bounce [animation-delay:240ms]">•</span>
          </span>
        ) : isUser ? (
          <span className="whitespace-pre-wrap">{m.text}</span>
        ) : (
          <span dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }} />
        )}
      </div>
    </div>
  );
}
