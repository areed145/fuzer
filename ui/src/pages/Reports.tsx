import { useState } from "react";
import { api } from "../api";
import { Markdown } from "../Markdown";
import { Status, href, useAsync } from "../lib";

export function ReportsPage({ query }: { query: URLSearchParams }) {
  const days = useAsync(() => api.dailyReports(), []);
  const day = query.get("day") ?? days.data?.[0]?.day;
  return (
    <section>
      <h1>Daily reports</h1>
      <p className="muted">
        What happened each production day and what it means, drafted from the day's records with every line cited.
        The ops lead reviews and publishes each one in Work.
      </p>
      <Status loading={days.loading && !days.data} error={days.error} empty={days.data?.length === 0}>
        No reports yet: `fuzer report` closes a day.
      </Status>
      {days.data && days.data.length > 0 && (
        <div className="row">
          {days.data.map((d) => (
            <a key={d.day} className={`badge${d.day === day ? " ok" : ""}`} href={href("/reports", { day: d.day })}
              title={d.ok ? "every line cited" : "has lines to check"}>
              {d.day}{d.version > 1 ? ` v${d.version}` : ""}{d.ok ? "" : " ⚠"}
            </a>
          ))}
        </div>
      )}
      {day && <Report key={day} day={day} />}
    </section>
  );
}

function Report({ day }: { day: string }) {
  const [version, setVersion] = useState<number | undefined>();
  const r = useAsync(() => api.dailyReport(day, version), [day, version]);
  const d = r.data;
  return (
    <>
      <Status loading={r.loading && !d} error={r.error} />
      {d && (
        <div className="grid">
          <section className="card">
            <div className="row small muted">
              <span>Version</span>
              {d.versions.map((v) => (
                <button key={v} className={v === d.version ? "chip on" : "chip"} onClick={() => setVersion(v)}>{v}</button>
              ))}
              <span>drafted by {d.drafted_by}</span>
              {d.usage?.cost_usd !== undefined && <span>${d.usage.cost_usd.toFixed(2)}</span>}
              {d.seconds !== null && <span>{d.seconds} s</span>}
              {d.item_id && <a href={href(`/work/item/${d.item_id}`)}>review in Work</a>}
            </div>
            <Markdown text={d.markdown} />
          </section>
          <section className="card">
            <h2>Checks</h2>
            {d.checks.ok ? <p className="small">Every line cites its records and every number is in them.</p> : (
              <ul className="small">
                {d.checks.uncited_lines.map((l) => <li key={l}>No citation: {l}</li>)}
                {d.checks.unknown_citations.map((c) => <li key={c}>Cites a record not in the day's facts: {c}</li>)}
                {d.checks.unknown_numbers.map((n) => <li key={n}>A number not in the records: {n}</li>)}
              </ul>
            )}
            {d.changes && (
              <>
                <h2>Changed since version {d.version - 1}</h2>
                {Object.entries(d.changes).map(([section, c]) => (
                  <div key={section} className="small">
                    <strong>{section}</strong>
                    {c.added.map((a) => <div key={a} className="ok">+ {a}</div>)}
                    {c.removed.map((a) => <div key={a} className="muted">− {a}</div>)}
                  </div>
                ))}
              </>
            )}
          </section>
        </div>
      )}
    </>
  );
}
