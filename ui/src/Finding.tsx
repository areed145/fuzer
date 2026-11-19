import "./styles/finding.css";
import { money, percent } from "./format";
import type { CaseSource, Finding, FindingItem } from "./api";
import { entityHref } from "./lib";
import { Card, Disclosure } from "./ui";
import { reportedBy, when } from "./work";
import { say, unit as wordUnit } from "./wording";

const ORIGIN: Record<Finding["origin"], string> = { deterministic: "Rule", agentic: "Agent", external: "Another system" };

function number(v: number): string {
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4 });
}

function Value({ item }: { item: FindingItem }) {
  const { kind, value, unit } = item;
  if (kind === "asset") return <a href={entityHref(String(value))}>{item.text}</a>;
  if (kind === "flag") return <>{value ? "yes" : "no"}</>;
  if (kind === "time") return <time dateTime={String(value)}>{when(String(value))}</time>;
  if (kind === "page" && String(value).startsWith("#/")) return <a href={String(value)}>{item.text ?? "Open"}</a>;
  if (kind === "link" && /^https?:\/\//.test(String(value))) return <a href={String(value)} target="_blank" rel="noopener noreferrer">{item.text ?? "Open"}</a>;
  return <>{typeof value === "number" ? number(value) : say(String(value))}{unit ? ` ${wordUnit(unit)}` : ""}</>;
}

const WHO: Record<Finding["origin"], string> = { deterministic: "A rule", agentic: "An agent", external: "Another system" };

/** Who reported an outside finding, where it names them (a field operator's report): "sam", not "Another system". */
const reporter = (finding: Finding): string | null => {
  const r = finding.origin === "external" ? finding.items.find((i) => i.label === "Reported by") : undefined;
  return r ? String(r.value) : null;
};

/** What was found, as one sentence a person reads first (#1082): who found it, when, what, and what is at stake.
 * "A rule found this on Dec 30, 2025, 9:20 PM: Low alarm on OK-J08-03 (peak 156 psig) ..., still going. $4,026 at
 * stake." The labelled fields stay one tap away. */
export function findingSentence(finding: Finding, title: string): string {
  const going = finding.items.find((i) => i.kind === "flag" && /still going/i.test(i.label));
  const at = finding.start ? ` on ${when(finding.start)}${finding.end ? ` to ${when(finding.end)}` : ""}` : "";
  const what = say(title).replace(/[.?\s]+$/, "");
  const stake = finding.value_usd != null && finding.value_usd > 0 ? ` ${money(finding.value_usd)} at stake.` : "";
  const sure = finding.confidence != null ? ` ${percent(finding.confidence)} confident.` : "";
  const who = reporter(finding);
  return `${who ? `${who} reported` : `${WHO[finding.origin]} found`} this${at}: ${what}${going ? (going.value ? ", still going" : ", now over") : ""}.${stake}${sure}`;
}

/** The one evidence component (P1.1): a finding reads the same on the case, the task and the phone. How it was
 * found comes first (rule, agent or another system, with the method and its standard), then when, then what was
 * found as labelled values with their units, times in local time and assets by name. */
export function FindingEvidence({ finding, checks = true }: { finding: Finding; checks?: boolean }) {
  return (
    <div className="finding">
      <dl className="kv small">
        <div><dt>Found by</dt><dd>{reporter(finding) ?? ORIGIN[finding.origin]}</dd></div>
        {finding.method && <div><dt>Method</dt><dd>{finding.method}</dd></div>}
        {finding.standard && <div><dt>Standard</dt><dd>{finding.standard}</dd></div>}
        {finding.start && <div><dt>Window</dt><dd><time dateTime={finding.start}>{when(finding.start)}</time>
          {finding.end ? <> to <time dateTime={finding.end}>{when(finding.end)}</time></> : null}</dd></div>}
        {finding.confidence != null && <div><dt>Confidence</dt><dd>{percent(finding.confidence)}</dd></div>}
        {finding.value_usd != null && <div><dt>Value at stake</dt><dd>{money(finding.value_usd)}</dd></div>}
        {finding.items.filter((item) => !(item.label === "Reported by" && reporter(finding))).map((item) => <div key={item.label}><dt>{item.label}</dt><dd><Value item={item} /></dd></div>)}
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
  if (all.length === 1) return (
    <Card className="case-findings" title="What was found">
      <p className="finding-sentence">{findingSentence(all[0].finding, all[0].title)}</p>
      <Disclosure className="evidence-item" label="How it was found and the values">
        <FindingEvidence finding={all[0].finding} />
      </Disclosure>
    </Card>
  );
  return (
    <Card className="case-findings" title={reportedBy(all)}>
      {all.map((x) => <Disclosure key={x.id} open className="evidence-item" label={<><strong>{x.label}</strong> from {x.source ?? "unknown"}: {x.title}{" "}
          <span className="muted small">· {when(x.first_seen)}{x.state !== "open" ? ` · ${x.state}` : ""}</span></>}>
        <FindingEvidence finding={x.finding} />
      </Disclosure>)}
    </Card>
  );
}
