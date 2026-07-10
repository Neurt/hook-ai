// Minimal, safe markdown -> HTML: escape first, then apply a few inline rules.
const escapeHtml = (s: string): string =>
  s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export function renderMarkdown(text: string): string {
  let h = escapeHtml(text);
  h = h.replace(/^### (.*)$/gm, '<h3 class="font-semibold text-navy mt-2 mb-1">$1</h3>');
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong class="text-navy">$1</strong>');
  // Italic: _text_ / *text* (word-boundary guarded so snake_case and URLs survive).
  h = h.replace(/(?<![\w/])_([^_\n]+)_(?!\w)/g, "<em>$1</em>");
  h = h.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
  h = h.replace(/`([^`]+)`/g, '<code class="px-1 rounded bg-cream border border-line text-navy">$1</code>');
  // [label](url) links first (mailto: opens the user's mail app with the draft);
  // then bare URLs — skipping ones already inside an href attribute.
  h = h.replace(
    /\[([^\]]+)\]\((mailto:[^\s)]+|https?:\/\/[^\s)]+)\)/g,
    '<a class="text-teal underline" href="$2" target="_blank" rel="noopener">$1</a>'
  );
  h = h.replace(
    /(?<!["=])(https?:\/\/[^\s<)]+)/g,
    '<a class="text-teal underline break-all" href="$1" target="_blank" rel="noopener">$1</a>'
  );
  h = h.replace(/\n/g, "<br/>");
  return h;
}

// Black-and-white serif resume renderer, matching the PDF/DOCX export:
// centered name, one contact line, bold-ruled sections, two-column entry rows.
const ENTRY_HEAD = /^\*\*(.+?)\*\*\s*\|\s*(.*)$/;
const ENTRY_SUB = /^\*(?!\*)([^*]+?)\*\s*\|\s*(.*)$/;

export function renderCvMarkdown(md: string): string {
  const inline = (s: string) => escapeHtml(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const html: string[] = [];
  const contact: string[] = [];
  let bullets: string[] = [];
  let seenSection = false;

  const flushBullets = () => {
    if (bullets.length) {
      html.push(
        '<ul class="my-1 ml-5 list-disc space-y-0.5">' +
          bullets.map((b) => `<li>${inline(b)}</li>`).join("") +
          "</ul>"
      );
      bullets = [];
    }
  };
  const flushContact = () => {
    if (contact.length) {
      html.push(`<p class="mb-2 text-center text-[12px]">${contact.map(escapeHtml).join(" | ")}</p>`);
      contact.length = 0;
    }
  };

  for (const raw of md.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    if (line.startsWith("# ")) {
      flushBullets();
      html.push(`<h2 class="mb-0.5 text-center text-[22px] leading-tight">${escapeHtml(line.slice(2))}</h2>`);
      continue;
    }
    if (line.startsWith("## ")) {
      flushBullets();
      flushContact();
      seenSection = true;
      const t = escapeHtml(line.slice(3).replace(/\*\*/g, "")).toUpperCase();
      html.push(`<h3 class="mt-3 mb-1 border-b border-black pb-0.5 text-[12px] font-bold uppercase tracking-wide">${t}</h3>`);
      continue;
    }
    if (/^[-*•]\s+/.test(line)) {
      bullets.push(line.replace(/^[-*•]\s+/, ""));
      continue;
    }
    flushBullets();

    const head = line.match(ENTRY_HEAD);
    if (head) {
      html.push(
        `<div class="flex justify-between gap-3"><span class="font-bold">${escapeHtml(head[1])}</span>` +
          `<span class="shrink-0 text-right">${escapeHtml(head[2])}</span></div>`
      );
      continue;
    }
    const sub = line.match(ENTRY_SUB);
    if (sub) {
      html.push(
        `<div class="flex justify-between gap-3 italic"><span>${escapeHtml(sub[1])}</span>` +
          `<span class="shrink-0 text-right">${escapeHtml(sub[2])}</span></div>`
      );
      continue;
    }
    if (!seenSection) {
      contact.push(line);
      continue;
    }
    html.push(`<p class="my-1">${inline(line)}</p>`);
  }
  flushBullets();
  flushContact();
  return html.join("");
}
