import type { AttachmentInfo } from "../types";

const LABELS: Record<AttachmentInfo["kind"], string> = {
  image: "Image",
  pdf: "PDF document",
  doc: "Document",
};

export default function AttachmentChip({ name, kind, previewUrl }: AttachmentInfo) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-white/10 px-3 py-2 text-white">
      {kind === "image" && previewUrl ? (
        <img src={previewUrl} alt={name} className="h-11 w-11 rounded-lg object-cover" />
      ) : (
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-white/15 text-[10px] font-bold tracking-wide">
          {kind.toUpperCase()}
        </div>
      )}
      <div className="min-w-0 text-left">
        <div className="max-w-[220px] truncate text-sm font-medium">{name}</div>
        <div className="text-xs text-white/70">{LABELS[kind]}</div>
      </div>
    </div>
  );
}
