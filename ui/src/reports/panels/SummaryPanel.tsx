/** The `summary` panel: the AI note for this report, scope, window and day, written by the summary specialist from the
 * report's own panel results and kept for everyone. The first visit with AI access to the report's default view (its
 * default window, ending today) writes it; another scope, window or day shows "Write the note" to someone with AI
 * access. A read only reads the kept note. */
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { ReportSummary, SummaryPanelResult } from "../../api/commentary";
import { usePermission } from "../../Auth";
import { ago } from "../../format";
import { entityHref, errorMessage, href } from "../../lib";
import { display } from "../../entityRefs";
import { useEntityNames } from "../../useEntityNames";
import { Badge, Button, ErrorText, List, Text } from "../../ui";
import { PanelFrame, usePanel } from "../panels";
import type { PanelProps } from "../spec";

function Cite({ c, names }: { c: string; names: ReturnType<typeof useEntityNames> }) {
  const n = /^case:(\d+)$/.exec(c);
  if (n) return <a href={href(`/cases/${n[1]}`)}>case {n[1]}</a>;
  if (c.startsWith("fz-")) return <a href={entityHref(c)}>{display(c, names)}</a>;
  if (/^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$/.test(c)) return <a href={href(`/metrics/${encodeURIComponent(c)}`)}>{c}</a>;
  return null;
}

/** What the checks found, in one line: the note is shown, flagged, not hidden. */
function checked(s: ReportSummary): string | null {
  const c = s.checks;
  if (!c || c.ok) return null;
  return [c.unknown_numbers.length > 0 && `${c.unknown_numbers.join(", ")} not on the page`,
    c.uncited_points.length > 0 && `${c.uncited_points.length} point${c.uncited_points.length > 1 ? "s" : ""} without a source`,
    c.unknown_citations.length > 0 && `cites ${c.unknown_citations.join(", ")}, not on the page`].filter(Boolean).join(" · ");
}

export function SummaryPanel(props: PanelProps) {
  const { report, panel, scope, window, at } = props;
  const key = report.definition.key;
  const ai = usePermission("ai");
  const period = { scope, window, at };
  const kept = usePanel<SummaryPanelResult>(props);
  const [written, setWritten] = useState<{ here: string; summary: ReportSummary } | null>(null);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef<string | null>(null);
  const here = `${key}|${period.scope}|${window}|${at}`;
  const summary = written?.here === here ? written.summary : kept.data?.summary ?? null;
  const names = useEntityNames([...new Set((summary?.points ?? []).flatMap((p) => p.cites).filter((c) => c.startsWith("fz-")))]);
  const write = (rewrite = false) => {
    asked.current = here;
    setWriting(true);
    setError(null);
    api.writeReportSummary(key, period, rewrite).then((r) => setWritten({ here, summary: r.summary }), (e) => setError(errorMessage(e)))
      .finally(() => setWriting(false));
  };
  useEffect(() => {
    // Once per period per visit, never retried, and only on the report's own view (the server says when: `due`).
    if (!ai || !kept.data?.due || !kept.data.can_write || asked.current === here) return;
    write();
  }, [ai, here, kept.data?.due, kept.data?.can_write]);
  const flag = summary && checked(summary);
  const canWrite = ai && !!kept.data?.can_write;
  return <PanelFrame title={panel.title ?? "Summary"} result={kept.data} loading={kept} className="report-summary"
    detail={summary ? <>Claude · {ago(summary.written_at)} · shared</> : undefined}>
    {summary && <>
      <Text as="p"><strong>{summary.headline}</strong></Text>
      {summary.points.length > 0 && <List>{summary.points.map((p, i) => <li key={i}>{p.text}
        {p.cites.length > 0 && <Text small muted> {p.cites.map((c, j) => <span key={c}>{j > 0 ? " · " : ""}<Cite c={c} names={names} /></span>)}</Text>}
      </li>)}</List>}
      {flag && <Text as="p" small><Badge tone="warn">Check</Badge> {flag}</Text>}
      {canWrite && !writing && <Button variant="link" size="sm" onClick={() => write(true)}>Write again</Button>}
    </>}
    {writing && <Text small muted>{summary ? "Writing a newer one…" : "Writing the note…"}</Text>}
    {!summary && !writing && kept.data && (canWrite
      ? <Button size="sm" onClick={() => write()}>Write the note</Button>
      : <Text small muted>No note for this period.</Text>)}
    <ErrorText>{error}</ErrorText>
  </PanelFrame>;
}
