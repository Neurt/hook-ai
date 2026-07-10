// Thin client for the Hook AI backend. nginx proxies /api -> the api service.
export interface Health {
  ok: boolean;
  model: string;
  detail: string;
}
export interface AtsChecklistItem {
  item: string;
  ok: boolean;
}
export interface AtsData {
  ats_cv_markdown: string;
  changes?: string[];
  ats_checklist?: AtsChecklistItem[];
  missing_keywords?: string[];
}
export interface ChatReply {
  session_id: string;
  reply: string;
  intent?: string;
  data?: unknown;
}
export interface UploadReply {
  session_id: string;
  reply: string;
  name?: string;
  skills?: string[];
}

// Persist the session id so a refresh (or reopening the tab) resumes the same
// server-side session: profile, job list, and chat history survive.
const sessionId = (() => {
  const KEY = "hookai_session_id";
  let id = "";
  try {
    id = localStorage.getItem(KEY) || "";
  } catch {
    /* storage blocked (private mode): fall back to per-load id */
  }
  if (!id) {
    id =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : String(Math.random()).slice(2);
    try {
      localStorage.setItem(KEY, id);
    } catch {
      /* non-persistent session is still functional */
    }
  }
  return id;
})();

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...body }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export type ExportFormat = "pdf" | "docx" | "md";

export interface HistoryReply {
  session_id: string;
  name: string;
  history: { role: "user" | "assistant"; text: string }[];
}

export const api = {
  health: () => fetch("/api/health").then((r) => r.json() as Promise<Health>),
  history: () =>
    fetch(`/api/session/${sessionId}/history`).then(
      (r) => r.json() as Promise<HistoryReply>
    ),
  chat: (message: string) => postJson<ChatReply>("/api/chat", { message }),
  exportCv: async (markdown: string, format: ExportFormat, filename = "ats-cv"): Promise<Blob> => {
    // Time out rather than spin forever if the backend is momentarily down.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const r = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ markdown, format, filename }),
        signal: controller.signal,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.blob();
    } finally {
      clearTimeout(timer);
    }
  },
  uploadCv: (file: File): Promise<UploadReply> => {
    const fd = new FormData();
    fd.append("session_id", sessionId);
    fd.append("file", file);
    return fetch("/api/cv/upload", { method: "POST", body: fd }).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<UploadReply>;
    });
  },
};
