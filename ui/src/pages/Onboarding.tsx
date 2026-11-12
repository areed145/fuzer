import { api, type OnboardingStep } from "../api";
import { Status, useAsync } from "../lib";
import { Badge, Button, Card } from "../ui";

const TONE: Record<OnboardingStep["status"], "ok" | "danger" | "warn"> = { ready: "ok", blocked: "danger", "to do": "warn" };

/** A new asset in a day (P8.4): each step from the seed bundle to the loop eval, whether it is ready, blocked or still
 * to do, and what each gap prevents. Reads only; each step links to the page that owns the fix. */
export function Onboarding() {
  const j = useAsync(() => api.onboarding(), []);
  return <div>
    <h2>Onboarding</h2>
    <p>Each step a new asset goes through, from its seed bundle to the loop eval. Viewing this page changes nothing.</p>
    <Button disabled={j.loading} onClick={j.reload}>Refresh</Button>
    <Status loading={j.loading && !j.data} error={j.error} />
    {j.data && <p className="small">
      {j.data.project ? `Project ${j.data.project}: ` : ""}{j.data.ready} ready, {j.data.blocked} blocked, {j.data.to_do} to do.
    </p>}
    {j.data?.steps.map((s) => <Card key={s.key} title={<>{s.title} <Badge tone={TONE[s.status]}>{s.status}</Badge></>}
      actions={s.link ? <a href={s.link}>Open</a> : undefined}>
      <p>{s.summary}</p>
      {s.gaps.length > 0 && <ul>{s.gaps.map((g, i) => <li key={i}>
        <strong>{g.link ? <a href={g.link}>{g.what}</a> : g.what}</strong>: {g.prevents}.
      </li>)}</ul>}
      {s.never_loaded && s.never_loaded.length > 0 && <p className="small">Never loaded: {s.never_loaded.join(", ")}.</p>}
      {s.command && <p className="small">Command: <code>{s.command}</code></p>}
    </Card>)}
  </div>;
}
