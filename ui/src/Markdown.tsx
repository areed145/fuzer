// A small Markdown renderer for assistant answers: headings, paragraphs, lists, tables, bold, inline
// code, fenced code and Mermaid diagrams. Builds React elements (never raw HTML), turns Fuzer ids (fz-tag-0123456789ab) into named links and
// chart markers ([[figure:f_...]]) into interactive charts and draft markers ([[work-draft:d_...]])
// into work a person can confirm.

import { Suspense, lazy, type ReactNode } from "react";
import { FigureView } from "./Figure";
import { WorkDraftCard } from "./WorkDraft";
import { TOKEN, TOKENS, display, idsIn, isId, linkCitations, type EntityNames } from "./entityRefs";
import { entityHref } from "./lib";
import { Table } from "./ui";
import { useEntityNames } from "./useEntityNames";

// Mermaid is large, so it loads only when an answer holds a diagram.
const MermaidDiagram = lazy(() => import("./MermaidDiagram").then((m) => ({ default: m.MermaidDiagram })));

// Agents sometimes write a diagram without its fence. The opening keyword and direction are specific
// enough to spot, and a false match still shows its text as source.
const DIAGRAM = /^(.*?[:.]\s+)?((?:flowchart|graph)\s+(?:TD|TB|BT|LR|RL)\b.*|(?:sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram)\s*)$/;
const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)/;
const INLINE = new RegExp(`(${TOKENS}|\\*\\*[^*]+\\*\\*|\`[^\`]+\`|fz-[a-z_]+-[0-9a-f]{12})`, "g");

const entity = (key: number, id: string, text: string) => <a key={key} href={entityHref(id)} title={id}>{text}</a>;

function makeInline(names: EntityNames | undefined) {
  const parts = (text: string): ReactNode[] => text.split(INLINE).map((part, i) => {
    const linked = part.match(TOKEN);
    if (linked) return entity(i, linked[1], linked[2]);
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{parts(part.slice(2, -2))}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) {
      const code = part.slice(1, -1);
      return isId(code) ? entity(i, code, display(code, names)) : <code key={i}>{code}</code>;
    }
    return isId(part) ? entity(i, part, display(part, names)) : part;
  });
  return (text: string) => parts(linkCitations(text, names));
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

const FIGURE = /\[\[(?:figure:f|work-draft:d)_[0-9a-f]+\]\]/g;

export function Markdown({ text }: { text: string }) {
  const inline = makeInline(useEntityNames(idsIn(text)));
  // Markers go on lines of their own.
  const lines = text.replace(/\r/g, "").replace(FIGURE, "\n$&\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fig = line.trim().match(/^\[\[figure:(f_[0-9a-f]+)\]\]$/);
    const fence = line.match(FENCE);
    const diagram = fence ? null : line.trim().match(DIAGRAM);
    const draft = line.trim().match(/^\[\[work-draft:(d_[0-9a-f]+)\]\]$/);
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (fence) {
      const close = new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
      const body: string[] = [];
      i++;
      while (i < lines.length && !close.test(lines[i])) body.push(lines[i++]);
      i++;  // an unclosed fence runs to the end, as in CommonMark
      const source = body.join("\n");
      out.push(fence[2].toLowerCase() === "mermaid"
        ? <Suspense key={i} fallback={<p className="small muted" role="status">Drawing the diagram…</p>}><MermaidDiagram source={source} /></Suspense>
        : <pre key={i}><code>{source}</code></pre>);
    } else if (diagram) {
      if (diagram[1]) out.push(<p key={`${i}-lead`}>{inline(diagram[1].trim())}</p>);
      const body = [diagram[2]];
      i++;
      while (i < lines.length && lines[i].trim()) body.push(lines[i++]);
      out.push(<Suspense key={i} fallback={<p className="small muted" role="status">Drawing the diagram…</p>}><MermaidDiagram source={body.join("\n")} /></Suspense>);
    } else if (fig) {
      out.push(<FigureView key={i} id={fig[1]} />);
      i++;
    } else if (draft) {
      out.push(<WorkDraftCard key={i} id={draft[1]} />);
      i++;
    } else if (h) {
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
          <Table variant="list" density="compact">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
          </Table>
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
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*([-*]|\d+[.)])\s|\s*\[\[(figure|work-draft):)/.test(lines[i]) && !FENCE.test(lines[i]) && !DIAGRAM.test(lines[i].trim())) {
        para.push(lines[i++].trim());
      }
      if (para.length === 0) para.push(lines[i++].trim());
      out.push(<p key={i}>{inline(para.join(" "))}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
