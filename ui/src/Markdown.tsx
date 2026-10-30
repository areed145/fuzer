// A small Markdown renderer for assistant answers: headings, paragraphs, lists, tables, bold, inline
// code. Builds React elements (never raw HTML), and turns Fuzer ids (fz-tag-0123456789ab) into links.

import type { ReactNode } from "react";
import { entityHref } from "./lib";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|fz-[a-z_]+-[0-9a-f]{12})/g;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{inline(part.slice(2, -2))}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) {
      const code = part.slice(1, -1);
      return /^fz-[a-z_]+-[0-9a-f]{12}$/.test(code) ? <a key={i} className="mono" href={entityHref(code)}>{code}</a>
        : <code key={i}>{code}</code>;
    }
    if (/^fz-[a-z_]+-[0-9a-f]{12}$/.test(part)) return <a key={i} className="mono" href={entityHref(part)}>{part}</a>;
    return part;
  });
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const level = Math.min(4, h[1].length + 2);
      const Tag = `h${level}` as "h3" | "h4";
      out.push(<Tag key={i}>{inline(h[2])}</Tag>);
      i++;
    } else if (line.trim().startsWith("|") && lines[i + 1]?.match(/^\s*\|?\s*:?-{2,}/)) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      out.push(
        <div key={i} className="md-table">
          <table className="list">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {
          items[items.length - 1] += " " + lines[i++].trim();
        }
      }
      const List = ordered ? "ol" : "ul";
      out.push(<List key={i}>{items.map((t, j) => <li key={j}>{inline(t)}</li>)}</List>);
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*([-*]|\d+[.)])\s)/.test(lines[i])) {
        para.push(lines[i++].trim());
      }
      if (para.length === 0) para.push(lines[i++].trim());
      out.push(<p key={i}>{inline(para.join(" "))}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
