import { Fragment, type ReactNode } from "react";

/* Minimal, dependency-free markdown for assistant answers: paragraphs, #-headings, **bold**, *italic*, `code`, "- " / "* " bullets and "1." lists.
   Renders React elements only (never raw HTML), so model output cannot inject markup. A line that is only bold text ("**Production:**")
   is shown as a small section label. */

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0, n = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={`${key}b${n++}`} className="font-semibold">{t.slice(2, -2)}</strong>);
    else if (t.startsWith("`")) out.push(<code key={`${key}c${n++}`} className="font-mono text-[12px] rounded bg-surface-2 px-1 py-px">{t.slice(1, -1)}</code>);
    else out.push(<em key={`${key}i${n++}`}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "label"; text: string }
  | { kind: "h"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

function parse(src: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => { if (para.length) { blocks.push({ kind: "p", text: para.join(" ") }); para = []; } };
  for (const raw of src.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trim();
    let m: RegExpMatchArray | null;
    if (!line) { flush(); continue; }
    if ((m = line.match(/^#{1,6}\s+(.*)$/))) { flush(); blocks.push({ kind: "h", text: m[1] }); continue; }
    if ((m = line.match(/^\*\*([^*]+?)\*\*:?$/))) { flush(); blocks.push({ kind: "label", text: m[1].replace(/:$/, "") }); continue; }
    if ((m = line.match(/^[-*•]\s+(.*)$/))) {
      flush(); const last = blocks[blocks.length - 1];
      if (last?.kind === "ul") last.items.push(m[1]); else blocks.push({ kind: "ul", items: [m[1]] });
      continue;
    }
    if ((m = line.match(/^\d+[.)]\s+(.*)$/))) {
      flush(); const last = blocks[blocks.length - 1];
      if (last?.kind === "ol") last.items.push(m[1]); else blocks.push({ kind: "ol", items: [m[1]] });
      continue;
    }
    para.push(line);
  }
  flush();
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  const blocks = parse(text);
  return (
    <div className="flex flex-col gap-1.5">
      {blocks.map((b, i) => {
        const k = `m${i}`;
        switch (b.kind) {
          case "label": return <div key={k} className={`text-[12px] font-medium text-muted ${i ? "mt-2.5" : ""}`}>{b.text}</div>;
          case "h": return <div key={k} className={`font-semibold ${i ? "mt-2" : ""}`}>{inline(b.text, k)}</div>;
          case "ul": return <ul key={k} className="list-disc pl-4 marker:text-muted flex flex-col gap-1">{b.items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ul>;
          case "ol": return <ol key={k} className="list-decimal pl-4 marker:text-muted flex flex-col gap-1">{b.items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ol>;
          default: return <p key={k}><Fragment>{inline(b.text, k)}</Fragment></p>;
        }
      })}
    </div>
  );
}

