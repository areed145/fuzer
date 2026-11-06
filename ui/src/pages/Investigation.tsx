import "../styles/investigation.css";
import { useEffect, useRef } from "react";
import { api, type Investigation, type InvestigationRecord } from "../api";
import { href, Status, timestamp, useAsync } from "../lib";
import { Badge, Button, ButtonLink, Card, EmptyState, PageHeader, Stat } from "../ui";

const STEPS = [
  { key: "issue", label: "Understand the issue" },
  { key: "asset", label: "Meet the asset" },
  { key: "evidence", label: "Inspect the evidence" },
  { key: "finding", label: "Read the finding" },
] as const;
type Step = typeof STEPS[number]["key"];

const journeyHref = (step: Step, evidence?: string) => href("/investigate/okan", { step, evidence });
const kindLabel = (kind: string) => ({
  downtime: "Downtime", notification: "Maintenance notification", log_entry: "Operator log",
  work_order: "Work order", event: "Extracted event",
}[kind] ?? kind.replace(/_/g, " "));

export function InvestigationPage({ query }: { query: URLSearchParams }) {
  const result = useAsync(() => api.okanInvestigation(), []);
  const selected = STEPS.findIndex((s) => s.key === query.get("step"));
  const index = selected < 0 ? 0 : selected;
  const step = STEPS[index];
  const evidence = query.get("evidence");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (step.key !== "evidence" || !result.data?.evidence.some((e) => e.id === evidence)) {
      heading.current?.focus({ preventScroll: true });
    }
  }, [step.key, !!result.data, evidence]);
  return (
    <section className="investigation">
      <PageHeader subtitle="Follow one issue from an asset to its source records and a finding you can check.">
        Okan investigation
      </PageHeader>
      <div className="investigation-context">
        <Badge>Synthetic demo data</Badge><Badge>Read-only investigation</Badge><Badge>No AI key needed</Badge>
        <p>Historical scenario: March 2024, with April maintenance follow-up. Not live plant conditions.</p>
      </div>
      <Status loading={result.loading && !result.data} error={result.error} />
      {result.error && (
        <EmptyState title="The investigation could not be opened">
          <p>Its loaded Okan records may be unavailable. Nothing was generated or changed.</p>
          <Button onClick={result.reload}>Try again</Button>{" "}
          <ButtonLink href={href("/")}>Return home</ButtonLink>
        </EmptyState>
      )}
      {!result.loading && !result.error && !result.data && (
        <EmptyState title="No investigation available"><a href={href("/")}>Return home</a></EmptyState>
      )}
      {result.data && !result.error && (
        <>
          <nav aria-label="Investigation steps">
            <ol className="investigation-steps">
              {STEPS.map((s, i) => (
                <li key={s.key}>
                  <a href={journeyHref(s.key)} aria-current={step.key === s.key ? "step" : undefined}>
                    <span aria-hidden="true">{i + 1}</span>{s.label}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
          <Card className="investigation-stage">
            <p className="muted small">Step {index + 1} of {STEPS.length}</p>
            <h2 id="investigation-step" tabIndex={-1} ref={heading}>{step.label}</h2>
            {step.key === "issue" && <Issue data={result.data} />}
            {step.key === "asset" && <Asset data={result.data} />}
            {step.key === "evidence" && <EvidenceList data={result.data} selected={evidence} />}
            {step.key === "finding" && <Findings data={result.data} />}
          </Card>
          <div className="investigation-controls">
            {index > 0 ? <ButtonLink href={journeyHref(STEPS[index - 1].key)}>Back: {STEPS[index - 1].label}</ButtonLink>
              : <ButtonLink href={href("/demo", { step: "evidence" })}>Return to Demo</ButtonLink>}
            {index < STEPS.length - 1
              ? <ButtonLink variant="primary" href={journeyHref(STEPS[index + 1].key)}>
                  Next: {STEPS[index + 1].label}
                </ButtonLink>
              : <ButtonLink variant="primary" href={href("/demo", { step: "agents" })}>Finish investigation</ButtonLink>}
          </div>
        </>
      )}
    </section>
  );
}

function Issue({ data }: { data: Investigation }) {
  return (
    <>
      <h3>{data.title}</h3>
      <p>Start with <strong>{data.asset_label}</strong>, the train B compressor named in this Okan scenario.
        What do the linked records say happened, what production loss was recorded, and what maintenance followed?</p>
      <div className="investigation-stats">
        <Stat label="Investigation window" value="March 2024" detail="Event dates, not today's operating state" />
        <Stat label="Connected evidence" value={data.evidence.length} detail="Loaded records, not an AI-generated answer" />
        <Stat label="What you will learn" value="Trace the evidence" detail="Asset → records → finding and limitations" />
      </div>
      <p>No setup, paid AI calls, or plant actions are needed. You can use browser Back, reload, or share
        this page without changing any records.</p>
    </>
  );
}

function Asset({ data }: { data: Investigation }) {
  return (
    <>
      <h3>{data.asset_label}</h3>
      <p>Fuzer connects records from different systems to one asset. These are the register's selected
        values, with the source that supplied each value.</p>
      {data.asset_fields.length ? (
        <dl className="investigation-fields">
          {data.asset_fields.map((f) => (
            <div key={f.name}><dt>{f.name}</dt><dd>{f.value} <Badge>{f.source}</Badge></dd></div>
          ))}
        </dl>
      ) : <p className="muted">No descriptive asset fields are available.</p>}
      <h3>Asset provenance</h3>
      <Provenance records={data.asset_records} />
      <p>Next, compare the operator accounts, maintenance records and recorded downtime.
        They describe different parts of the same investigation; a link alone is not proof of cause.</p>
    </>
  );
}

function Provenance({ records }: { records: InvestigationRecord[] }) {
  if (!records.length) return <p className="muted">No direct source-record link is available for this derived record.</p>;
  return (
    <ul className="investigation-provenance">
      {records.map((r) => (
        <li key={`${r.source}:${r.record_type}:${r.key}`}>
          <strong>{r.source}</strong> / {r.record_type} / <code>{r.key}</code>
          <span className="muted">Last observed during ingestion: <time dateTime={r.observed_at}>
            {timestamp(r.observed_at)}
          </time></span>
        </li>
      ))}
    </ul>
  );
}

function EvidenceList({ data, selected }: { data: Investigation; selected: string | null }) {
  return (
    <>
      <p>Open each record to inspect its text and source identity. Event dates use the source's date/time;
        no timezone is assumed when the source did not supply one. Ingestion timestamps below are UTC,
        not a claim that the historical data is current.</p>
      {data.evidence.length ? data.evidence.map((e, i) => (
        <EvidenceCard key={e.id} evidence={e} number={i + 1} selected={selected === e.id} />
      )) : <EmptyState title="No supporting evidence available">Return home or try opening the investigation again.</EmptyState>}
    </>
  );
}

function EvidenceCard({ evidence: e, number, selected }: {
  evidence: Investigation["evidence"][number]; number: number; selected: boolean;
}) {
  const summary = useRef<HTMLElement>(null);
  useEffect(() => { if (selected) summary.current?.focus(); }, [selected]);
  return (
    <details className="investigation-evidence" open={selected || undefined}>
      <summary ref={summary}>
        <span><strong>Evidence {number}</strong> · {kindLabel(e.kind)} · <time>{e.at ?? "Date unknown"}</time></span>
        <span>{e.text ?? e.label}</span>
      </summary>
      {!e.text && <p className="section-text">No record text is available.</p>}
      {e.lost_oil_sm3 !== null && <p><strong>{e.lost_oil_sm3.toLocaleString()} Sm³ recorded lost oil</strong>
        {" "}(standard cubic metres, not Sm³/day)</p>}
      <Provenance records={e.records} />
      <p className="muted small">Register record: <code>{e.label}</code></p>
    </details>
  );
}

function Findings({ data }: { data: Investigation }) {
  return (
    <>
      <h3>What the loaded records establish</h3>
      <p>This finding comes directly from the loaded evidence, without an AI-generated answer.</p>
      {data.findings.map((f, i) => (
        <div className="investigation-finding" key={i}>
          <p>{f.text}</p>
          <div className="row" aria-label={`Sources for finding ${i + 1}`}>
            {f.evidence_ids.map((id) => {
              const n = data.evidence.findIndex((e) => e.id === id);
              return <a key={id} href={journeyHref("evidence", id)}>Evidence {n + 1}</a>;
            })}
          </div>
        </div>
      ))}
      <h3>What this does not establish</h3>
      <ul>{data.limitations.map((text) => <li key={text}>{text}</li>)}</ul>
      <p><strong>Investigation complete.</strong> You have traced an asset to its supporting records and a
        finding with explicit limits. An engineer would review the cited records and repair outcome before
        deciding any next action. No work was created and no plant action was taken.</p>
    </>
  );
}
