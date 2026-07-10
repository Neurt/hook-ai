import { useEffect, useState } from "react";
import { api } from "../api";
import logo from "../image/logo.png";

export default function Header() {
  const [status, setStatus] = useState("connecting…");
  const [ok, setOk] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      api
        .health()
        .then((h) => {
          if (cancelled) return;
          setOk(h.ok);
          setStatus(h.ok ? `ready · ${h.model}` : "no API key");
        })
        .catch(() => {
          if (cancelled) return;
          setOk(false);
          setStatus("offline");
        });
    check();
    // Re-check periodically so a transient backend restart can't leave a stale badge.
    const timer = setInterval(check, 12000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <header className="flex h-16 items-center justify-between border-b border-line bg-cream px-5">
      <img src={logo} alt="Hook AI — agent-assisted CV & job search" className="h-12 w-auto" />
      <span
        className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${
          ok ? "border-teal/40 bg-teal/5 text-teal" : "border-line text-slate-400"
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-teal" : "bg-slate-400"}`} />
        {status}
      </span>
    </header>
  );
}
