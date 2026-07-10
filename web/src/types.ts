// Shared frontend-only types (not part of the API contract).
export interface AttachmentInfo {
  name: string;
  kind: "image" | "pdf" | "doc";
  previewUrl?: string;
}

export function fileKind(file: File): AttachmentInfo["kind"] {
  if (file.type.startsWith("image/")) return "image";
  if (file.name.toLowerCase().endsWith(".pdf")) return "pdf";
  return "doc";
}
