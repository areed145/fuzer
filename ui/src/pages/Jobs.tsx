import "../styles/jobs.css";
import { Button, ButtonLink, PageHeader, Table, Card as UICard , Badge as UIBadge, Disclosure } from "../ui";
// The Jobs page (#208): what Fuzer's scheduled Dagster jobs did, without opening Dagster.
import { api, type DetectorTier, type JobCheck, type JobRunSummary, type JobTrigger, type JobsOverview } from "../api";
import { Status, href, timestamp, useAsync } from "../lib";

const RUN_CLASS: Record<string, string> = {
  SUCCESS: "ok", FAILURE: "fail", CANCELED: "fail", STARTED: "updating", STARTING: "updating", QUEUED: "key",
  NOT_STARTED: "key", CANCELING: "updating",
};
const CHECK_LABEL: Record<string, string> = { pass: "pass", warn: "warn", fail: "fail", running: "running", not_run: "not run" };

/** "3 min ago", "in 2 h", or a date beyond a week. */
export function when(iso: string | null | undefined): string {
  if (!iso || Number.isNaN(new Date(iso).getTime())) return "Time unknown";
  const t = new Date(iso).getTime();
  const s = (t - Date.now()) / 1000;
  const a = Math.abs(s);
  const span = a < 90 ? `${Math.round(a)} s` : a < 5400 ? `${Math.round(a / 60)} min` : a < 129600 ? `${Math.round(a / 3600)} h`
    : a < 7 * 86400 ? `${Math.round(a / 86400)} d` : null;
  if (!span) return timestamp(iso);
  return s < 0 ? `${span} ago` : `in ${span}`;
}

const duration = (sec: number | null) =>
  sec == null ? "" : sec < 90 ? `${Math.round(sec)} s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;

const runHref = (id: string) => href(`/jobs/run/${id}`);

export function RunBadge({ status }: { status: string }) {
  return <UIBadge tone={status === "SUCCESS" ? "ok" : status === "FAILURE" ? "danger" : status === "CANCELED" ? "warn" : "muted"}>{status.toLowerCase().replace(/_/g, " ")}</UIBadge>;
}

export function JobsPage() {
  const o = useAsync(() => api.jobs(), []);
  return (
    <section className="jobs">
      <div className="row spread">
        <PageHeader subtitle="Scheduled loads, rebuilds and scans.">Jobs</PageHeader>
        {o.data && <DagsterLink d={o.data.dagster} />}
      </div>
      <Status loading={o.loading && !o.data} error={o.error} onRetry={o.reload} />
      {o.data && !o.data.available && (
        <UICard className="">
          <p>Scheduler history is unavailable in this deployment. A deployment operator can check the scheduler service.</p>
          <p className="small">Running Fuzer yourself? Start the scheduler with <code>fuzer up</code> (without{" "}
            <code>--no-jobs</code>), or on its own with <code>fuzer jobs</code>.</p>
          <p><a href={href("/sources")}>Inspect recorded source loads</a> or <a href={href("/browse")}>Explore loaded records</a>.</p>
          <Button disabled={o.loading} onClick={o.reload}>Refresh scheduler history</Button>
        </UICard>
      )}
      {o.data?.available && <Overview o={o.data} />}
      <Tiers />
    </section>
  );
}

/** Each detector tier: what runs in it, and its runs, time and findings over the last day (P3.2). */
function Tiers() {
  const t = useAsync(() => api.jobTiers(), []);
  const rows = (t.data?.tiers ?? []).filter((x) => x.detectors.length);
  if (!rows.length) return null;
  return <>
    <h2>Detector tiers</h2>
    <div className="scroll-x">
      <Table variant="list">
        <thead><tr><th>Tier</th><th>Detectors</th><th>Last run</th><th>Last day</th></tr></thead>
        <tbody>{rows.map((x) => <TierRow key={x.tier} x={x} />)}</tbody>
      </Table>
    </div>
  </>;
}

function TierRow({ x }: { x: DetectorTier }) {
  return <tr>
    <td>{x.label}</td>
    <td className="small">{x.detectors.map((d) => d.title).join(", ")}</td>
    <td className="small">{x.last_at ? when(x.last_at) : <span className="muted">never</span>}</td>
    <td className="small">{x.runs ? <>{x.runs} runs, {duration(x.seconds)} in all ({duration(x.avg_seconds)} each), {x.found} found
      {x.failures > 0 && <span className="error">, {x.failures} failed</span>}</> : <span className="muted">no runs</span>}</td>
  </tr>;
}

function DagsterLink({ d }: { d: JobsOverview["dagster"] }) {
  return d.reachable ? (
    <ButtonLink href={d.url} target="_blank" rel="noreferrer">Open in Dagster ↗</ButtonLink>
  ) : (
    <span className="muted small" title={d.url}>Dagster UI not reachable</span>
  );
}

function Overview({ o }: { o: JobsOverview }) {
  return (
    <>
      <div className={`banner health-${o.health.level}`} role="status">
        <i className="dot" aria-hidden="true" />
        <div>
          <strong>{o.health.summary}</strong>
          {!o.scheduler.running && (
            <div className="small">
              The scheduler reports no running daemon, so schedules and sensors may not fire.
              {" "}Last recorded heartbeat: {timestamp(o.scheduler.last_heartbeat)}.
              Ask a deployment operator to inspect the scheduler service; retrying a source load does not repair it.
            </div>
          )}
        </div>
      </div>

      <h2>Jobs</h2>
      <div className="scroll-x">
        <Table variant="results" className="first-column-wide">
          <thead>
            <tr><th>Job</th><th>Runs when</th><th>Last run</th><th>Next</th><th>Recent runs</th></tr>
          </thead>
          <tbody>
            {o.jobs.map((j) => (
              <tr key={j.name} className="static">
                <td>
                  <code>{j.name}</code>
                  {j.description && <div className="muted small">{j.description}</div>}
                </td>
                <td>{j.triggers.length ? j.triggers.map((t) => <Trigger key={t.name} t={t} />) : <span className="muted small">manual only</span>}</td>
                <td>
                  {j.last_run ? (
                    <>
                      <a href={runHref(j.last_run.run_id)} aria-label={`Inspect ${j.name} last run`}><RunBadge status={j.last_run.status} /></a>
                      <div className="muted small">{when(j.last_run.start ?? j.last_run.end)}{j.last_run.seconds != null && ` · ${duration(j.last_run.seconds)}`}</div>
                    </>
                  ) : <span className="muted small">never run</span>}
                </td>
                <td className="small">{j.triggers.map((t) => t.next_tick).filter(Boolean).map((n) => when(n)).join(", ") || <span className="muted">—</span>}</td>
                <td><RunDots runs={j.recent_runs} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      <h2>Data checks</h2>
      <div className="scroll-x">
        <Table variant="list" density="compact">
          <thead><tr><th>Check</th><th>Result</th><th>Details</th><th>Checked</th></tr></thead>
          <tbody>
            {o.checks.map((c) => <CheckRow key={`${c.asset}.${c.name}`} c={c} />)}
          </tbody>
        </Table>
      </div>

      <h2>Sensors</h2>
      <div className="scroll-x">
        <Table variant="list">
          <thead><tr><th>Sensor</th><th>State</th><th>Last evaluation</th><th>Outcome</th></tr></thead>
          <tbody>
            {o.sensors.map((s) => (
              <tr key={s.name}>
                <td><code>{s.name}</code>{s.description && <div className="muted small">{s.description}</div>}</td>
                <td><OnOff on={s.status === "on"} /></td>
                <td className="small">{s.last_tick ? when(s.last_tick.time) : <span className="muted">never</span>}</td>
                <td className="small">
                  {s.last_tick?.error ? <span className="error">{s.last_tick.error}</span>
                    : s.last_tick?.skip_reason ? <span className="muted">Skipped: {s.last_tick.skip_reason}</span>
                    : s.last_tick?.run_keys.length ? <>Started a run for {runKeyNames(s.last_tick.run_keys)}</>
                    : s.last_tick ? s.last_tick.status.toLowerCase() : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      {o.claude && <ClaudeSpend c={o.claude} />}
    </>
  );
}

/** Sensor run keys are JSON maps of the exports that changed; show their names. */
function runKeyNames(keys: string[]): string {
  const names = keys.flatMap((k) => {
    try {
      return Object.keys(JSON.parse(k));
    } catch {
      return [k];
    }
  });
  return names.join(", ");
}

function Trigger({ t }: { t: JobTrigger }) {
  return (
    <div className="trigger">
      <OnOff on={t.status === "on"} />
      {t.kind === "schedule" ? <code title={t.timezone ?? undefined}>{t.cron}</code> : <span>sensor <code>{t.name}</code></span>}
    </div>
  );
}

function OnOff({ on }: { on: boolean }) {
  return <UIBadge className={`badge ${on ? "ok" : "key"}`}>{on ? "on" : "off"}</UIBadge>;
}

function RunDots({ runs }: { runs: JobRunSummary[] }) {
  if (!runs.length) return <span className="muted small">—</span>;
  return (
    <span className="run-dots">
      {[...runs].reverse().map((r) => (
        <a key={r.run_id} href={runHref(r.run_id)} className={`run-dot run-${RUN_CLASS[r.status] ?? "key"}`}
          title={`${r.status.toLowerCase()} · ${r.start ? new Date(r.start).toLocaleString() : "not started"}`}
          aria-label={`${r.status.toLowerCase()} run ${when(r.start)}`} onClick={(e) => e.stopPropagation()} />
      ))}
    </span>
  );
}

function CheckRow({ c }: { c: JobCheck }) {
  const examples = Array.isArray(c.metadata.examples) ? (c.metadata.examples as unknown[]) : [];
  const rest = Object.entries(c.metadata).filter(([k]) => k !== "examples");
  return (
    <tr>
      <td><code>{c.name}</code>{c.description && <div className="muted small">{c.description}</div>}</td>
      <td><UIBadge tone={c.level === "pass" ? "ok" : c.level === "fail" ? "danger" : c.level === "warn" ? "warn" : "muted"}>{CHECK_LABEL[c.level] ?? "unknown"}</UIBadge></td>
      <td className="small">
        {c.note && <div className={c.level === "fail" ? "error" : "muted"}>{c.note}</div>}
        {rest.length > 0 && <div>{rest.map(([k, v]) => `${k.replace(/_/g, " ")}: ${fmtValue(v)}`).join(" · ")}</div>}
        {examples.length > 0 && (
          <div className="examples">
            {examples.slice(0, 8).map((ex, i) => {
              const label = exampleLabel(ex);
              return label ? <a key={i} href={href("/browse", { q: label })}>{label}</a> : <code key={i}>{JSON.stringify(ex).slice(0, 60)}</code>;
            })}
            {examples.length > 8 && <span className="muted">+{examples.length - 8} more</span>}
          </div>
        )}
      </td>
      <td className="small">
        {c.run_id ? <a href={runHref(c.run_id)}>{when(c.time)}</a> : <span className="muted">never</span>}
      </td>
    </tr>
  );
}

/** A check example is a label, or a row with one: link it to a search. */
function exampleLabel(ex: unknown): string | null {
  if (typeof ex === "string") return ex;
  if (ex && typeof ex === "object") {
    const o = ex as Record<string, unknown>;
    const v = o.label ?? o.tag ?? o.number ?? o.src_label ?? o.src;
    return typeof v === "string" ? v : null;
  }
  return null;
}

const fmtValue = (v: unknown) =>
  typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 3 })
    : typeof v === "object" ? JSON.stringify(v).slice(0, 60) : String(v);

function ClaudeSpend({ c }: { c: NonNullable<JobsOverview["claude"]> }) {
  return (
    <>
      <h2>Claude spend</h2>
      <UICard className="">
        <p className="small">
          The nightly <code>claude_reading</code> job is <OnOff on={c.schedule === "on"} />
          {c.schedule !== "on" && " (off by default because it costs money; turn it on in Dagster)"}.
          {" "}Last 30 days: <strong>${c.usd_30d.toFixed(2)}</strong> across {c.runs.length} run{c.runs.length === 1 ? "" : "s"}.
        </p>
        {Object.keys(c.limits).length > 0 && (
          <p className="small">
            Limits per run: {Object.entries(c.limits).map(([k, v]) => limitText(k, v)).join(" · ")}
          </p>
        )}
        {c.runs.length > 0 && (
          <Table variant="list" density="compact">
            <thead><tr><th>Run</th><th>Status</th><th>Model</th><th>Tokens in / out</th><th>Cost</th></tr></thead>
            <tbody>
              {c.runs.map((r) => (
                <tr key={r.run_id}>
                  <td><a href={runHref(r.run_id)}>{r.start ? new Date(r.start).toLocaleString() : r.run_id.slice(0, 8)}</a></td>
                  <td><RunBadge status={r.status} /></td>
                  <td><code>{r.model || "—"}</code></td>
                  <td>{r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}</td>
                  <td>${r.usd.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </UICard>
    </>
  );
}

function limitText(key: string, v: number | boolean): string {
  if (key === "batch") return v ? "batch requests (half price)" : "no batching";
  if (key === "max_sre_usd") return `$${v} per SRE run`;
  return `up to ${Number(v).toLocaleString()} ${key.replace(/^max_/, "").replace(/_/g, " ")}`;
}

export function JobRunPage({ id }: { id: string }) {
  const r = useAsync(() => api.jobRun(id), [id]);
  if (!r.data) return <section><PageHeader>Job run</PageHeader>
    <Status loading={r.loading} error={r.error} onRetry={r.reload} />
    {r.error && <p>The scheduler run could not be read. <a href={href("/jobs")}>Return to Jobs</a> to inspect available history.</p>}
  </section>;
  const d = r.data;
  const tags = Object.entries(d.tags).filter(([k]) => k !== "dagster/failure_reason");
  return (
    <section className="jobs">
      <div className="row">
        <PageHeader><code>{d.job}</code></PageHeader>
        <RunBadge status={d.status} />
      </div>
      <p className="small">
        Run <code>{d.run_id.slice(0, 8)}</code> · started by {d.trigger}
        {" · Scheduler start: "}{timestamp(d.start)}
        {d.seconds != null && ` · took ${duration(d.seconds)}`}
      </p>
      <p className="small"><a href={href("/jobs")}>Return to Jobs</a>{" · "}
        <a href={href("/sources")}>Inspect source Runs and Quality</a>. Scheduler times describe execution, not source event dates.</p>
      {["FAILURE", "CANCELED"].includes(d.status) && <p className="notice">This execution did not complete successfully.
        Inspect errors and recorded source runs before deliberately retrying in Sources; some earlier steps may have completed.</p>}

      {d.errors.length > 0 && (
        <>
          <h2>What went wrong</h2>
          {d.errors.map((e, i) => (
            <UICard key={i} className="error-card">
              <div className="small muted">{e.step ? <>step <code>{e.step}</code></> : "run"}{e.error_class && <> · {e.error_class}</>}</div>
              <pre className="data">{e.message}</pre>
              {e.stack && (
                <Disclosure label={<>Stack trace</>}>
                  <pre className="data">{e.stack}</pre>
                </Disclosure>
              )}
            </UICard>
          ))}
        </>
      )}

      {d.assets.length > 0 && (
        <>
          <h2>Recorded asset results</h2>
          <div className="grid">
            {d.assets.map((a) => (
              <UICard key={a.asset} className="">
                <h3><code>{a.asset}</code></h3>
                <p className="small">Scheduler observation: {timestamp(a.time)}</p>
                <Table variant="keyValue" density="compact" headingCase="verbatim">
                  <tbody>
                    {Object.entries(a.metadata).map(([k, v]) => (
                      <tr key={k}><th>{k.replace(/_/g, " ")}</th><td>{fmtValue(v)}</td></tr>
                    ))}
                  </tbody>
                </Table>
              </UICard>
            ))}
          </div>
        </>
      )}

      {tags.length > 0 && (
        <>
          <h2>Tags</h2>
          <Table variant="keyValue" density="compact" headingCase="verbatim">
            <tbody>{tags.map(([k, v]) => <tr key={k}><th>{k}</th><td><code>{v}</code></td></tr>)}</tbody>
          </Table>
        </>
      )}

      <h2>Log (last {d.log_tail.length})</h2>
      <div className="scroll-x">
        <Table variant="list" density="compact" className="wrap-last-column">
          <thead><tr><th>Time</th><th>Level</th><th>Step</th><th>Message</th></tr></thead>
          <tbody>
            {d.log_tail.map((l, i) => (
              <tr key={i} className={l.level === "ERROR" || l.level === "CRITICAL" ? "log-error" : undefined}>
                <td className="mono" title={l.time ?? undefined}>{timestamp(l.time, "seconds")}</td>
                <td>{l.level}</td>
                <td className="mono">{l.step ?? ""}</td>
                <td>{l.message}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </section>
  );
}
