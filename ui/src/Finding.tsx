import "./styles/finding.css";
import type { CaseSource, Finding, FindingItem } from "./api";
import { entityHref } from "./lib";
import { Card, Disclosure } from "./ui";
import { reportedBy, when } from "./work";

const ORIGIN: Record<Finding["origin"], string> = { deterministic: "Rule", agentic: "Agent", external: "Another system" };

function number(v: number): string {
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4 });
}

function Value({ item }: { item: FindingItem }) {
  const { kind, value, unit } = item;
  if (kind === "asset") return <a href={entityHref(String(value))}>{item.text}</a>;
  if (kind === "flag") return <>{value ? "yes" : "no"}</>;
  if (kind === "time") return <time dateTime={String(value)}>{when(String(value))}</time>;
  return <>{typeof value === "number" ? number(value) : String(value)}{unit ? ` ${unit}` : ""}</>;
}

/** The one evidence component (P1.1): a finding reads the same on the case, the task and the phone. How it was
 * found comes first (rule, agent or another system, with the method and its standard), then when, then what was
 * found as labelled values with their units, times in local time and assets by name. */
export function FindingEvidence({ finding, checks = true }: { finding: Finding; checks?: boolean }) {
  return (
    <div className="finding">
      <dl className="kv small">
        <div><dt>Found by</dt><dd>{ORIGIN[finding.origin]}</dd></div>
        {finding.method && <div><dt>Method</dt><dd>{finding.method}</dd></div>}
        {finding.standard && <div><dt>Standard</dt><dd>{finding.standard}</dd></div>}
        {finding.start && <div><dt>Window</dt><dd><time dateTime={finding.start}>{when(finding.start)}</time>
          {finding.end ? <> to <time dateTime={finding.end}>{when(finding.end)}</time></> : null}</dd></div>}
        {finding.confidence != null && <div><dt>Confidence</dt><dd>{Math.round(finding.confidence * 100)}%</dd></div>}
        {finding.value_usd != null && <div><dt>Value at stake</dt><dd>${Math.round(finding.value_usd).toLocaleString()}</dd></div>}
        {finding.items.map((item) => <div key={item.label}><dt>{item.label}</dt><dd><Value item={item} /></dd></div>)}
      </dl>
      {checks && finding.checks.length > 0 && <>
        <h4 className="small">Checks before the work</h4>
        <ol className="small">{finding.checks.map((c) => <li key={c}>{c}</li>)}</ol>
      </>}
    </div>
  );
}

/** What was found, from every system that reported the event this case works, so the decision weighs them together
 * (#464, P1.1). One source reads as its finding; several are listed, each with its own. */
export function CaseFindings({ sources }: { sources?: CaseSource[] }) {
  const all = sources ?? [];
  if (!all.length) return null;
  if (all.length === 1) return <Card className="case-findings" title="What was found"><FindingEvidence finding={all[0].finding} /></Card>;
  return (
    <Card className="case-findings" title={reportedBy(all)}>
      {all.map((x) => <Disclosure key={x.id} open className="evidence-item" label={<><strong>{x.label}</strong> from {x.source ?? "unknown"}: {x.title}{" "}
          <span className="muted small">· {when(x.first_seen)}{x.state !== "open" ? ` · ${x.state}` : ""}</span></>}>
        <FindingEvidence finding={x.finding} />
      </Disclosure>)}
    </Card>
  );
}
