import { ActionStatus, ConfirmAction, Badge as UIBadge} from "../../ui";
/** Shared by the Sources pages (#225): who is acting, small formatting helpers, async buttons, badges, a line diff
 * and the "rebuild needed" banner. */
import { useState } from "react";
import { api, type SourceHealth, type SourceLoad } from "../../api";
import { notifyChanged, timestamp, useAsync } from "../../lib";
import { loadPerson } from "../../work";

export const reviewer = () => loadPerson() || "anonymous";

export const STATE: Record<string, string> = {
  ok: "ok", done: "ok", unchanged: "ok", new: "key", "not loaded": "key", changed: "updating", stale: "", held: "",
  failed: "", unmapped: "",
};

export const cell = (v: unknown) =>
  v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v);
export const when = timestamp;

export const SCHEDULES: [string, string][] = [
  ["", "with the daily refresh (02:00) and when its file changes"], ["0 * * * *", "hourly"],
  ["0 6 * * *", "daily at 06:00"], ["0 6 * * 1", "weekly, Monday 06:00"],
];
export const scheduleText = (cron: string | null | undefined) =>
  SCHEDULES.find(([c]) => c === (cron ?? ""))?.[1] ?? `cron ${cron}`;

export const LOAD_HELP: Record<string, string> = {
  snapshot: "each delivery is the whole table: records no longer in it are removed",
  incremental: "deliveries add and change records; nothing is removed",
  append: "only rows at or after the last watermark value are read (lake tables)",
};
export const DRIFT_HELP: Record<string, string> = {
  hold_breaking: "hold the load when a column is removed or changes type (recommended)",
  hold_any: "hold the load on any change, new columns too",
  ignore: "load anyway",
};

/** A button's async action with its busy and error state; every success tells the other views to refresh. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(name: string, fn: () => Promise<T>, after?: (r: T) => void) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      after?.(r);
      notifyChanged();
      return r;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run };
}

export function HealthBadge({ h }: { h: SourceHealth | null | undefined }) {
  if (!h) return <UIBadge>Health unknown</UIBadge>;
  return <UIBadge tone={h.state === "ok" ? "ok" : h.state === "failed" ? "danger"
    : ["stale", "held", "checks failing", "profile drift"].includes(h.state) ? "warn" : "muted"}
    title={h.problems.join(", ") || "Recorded source health; not a live plant status"}>{h.state === "ok" ? "No reported problems" : h.state}</UIBadge>;
}

export function Loaded({ r }: { r: SourceLoad }) {
  if (r.paused?.length) return <ActionStatus><p>No load: {r.paused.join(", ")} is paused. Resume it in Sources before retrying.</p></ActionStatus>;
  if (r.held.length) return <ActionStatus><p>Load held: records were not changed for {r.held.join(", ")}.
    Open the dataset's Quality and Runs tabs, resolve the reported problem, then deliberately retry.</p></ActionStatus>;
  if (r.unchanged.length) return <ActionStatus><p>Unchanged since the last load: nothing to ingest.</p></ActionStatus>;
  return (
    <p role="status" className="small">
      Loaded {r.records.toLocaleString()} records ({r.inserted} new, {r.updated} changed, {r.removed} removed).
      {" "}Review the dataset's Runs and freshness; apply any required rebuild shown in Sources.
    </p>
  );
}

export function TestSteps({ steps }: { steps: { step: string; ok: boolean; ms: number; detail: unknown }[] }) {
  return (
    <ul className="small">
      {steps.map((s) => (
        <li key={s.step}><UIBadge className={`badge ${s.ok ? "ok" : ""}`}>{s.ok ? "ok" : "failed"}</UIBadge> {s.step}{" "}
          <span className="muted">{s.ms} ms · {cell(s.detail)}</span></li>
      ))}
    </ul>
  );
}

/** Line diff (longest common subsequence): what a new mapping version changes. */
export function lineDiff(a: string, b: string): { op: " " | "-" | "+"; line: string }[] {
  const x = a.split("\n"), y = b.split("\n");
  const n = x.length, m = y.length;
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: { op: " " | "-" | "+"; line: string }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push({ op: " ", line: x[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: "-", line: x[i++] });
    else out.push({ op: "+", line: y[j++] });
  }
  while (i < n) out.push({ op: "-", line: x[i++] });
  while (j < m) out.push({ op: "+", line: y[j++] });
  return out;
}

export function Diff({ from, to }: { from: string; to: string }) {
  const d = lineDiff(from, to);
  if (!d.some((x) => x.op !== " ")) return <p className="muted small">No change.</p>;
  return (
    <pre className="diff mono small">
      {d.map((x, i) => <div key={i} className={x.op === "+" ? "add" : x.op === "-" ? "del" : "same"}>{x.op} {x.line}</div>)}
    </pre>
  );
}

/** Loads, mapping versions or rules that changed since the register was built: one place to rebuild. */
export function RebuildBanner() {
  const s = useAsync(() => api.sourceStatus(), []);
  const a = useAction();
  if (!s.data) return null;
  const running = s.data.running?.running || s.data.running?.pending;
  if (!s.data.needed && !running) return null;
  return (
    <div className="notice small row spread">
      <span>
        {running ? "Rebuilding the register…" : <>Not in the register yet: {s.data.changes.map((c) =>
          `${c.count} ${c.what === "loads" ? "load" : c.what === "mappings" ? "mapping version" : "rule change"}${c.count === 1 ? "" : "s"}`)
          .join(", ")} since it was built{s.data.built_at ? ` (${when(s.data.built_at)})` : ""}. A rebuild turns them into
          entities, links and values.</>}
      </span>
      {!running && <ConfirmAction label="Rebuild now" busy={!!a.busy}
        consequence="Rebuild shared facility entities, links and values from loaded records and current mappings/rules? Follow the existing Updating/health indicator for completion."
        onConfirm={() => a.run("rebuild", () => api.rematch(), () => {
        setTimeout(s.reload, 1500);
      })} />}
      <ActionStatus busy={a.busy ? "Requesting facility rebuild…" : running ? "Rebuild pending or running; completion is not yet confirmed." : null} error={a.error} />
    </div>
  );
}
