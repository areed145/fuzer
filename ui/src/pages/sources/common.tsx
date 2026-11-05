/** Shared by the Sources pages (#225): who is acting, small formatting helpers, async buttons, badges, a line diff
 * and the "rebuild needed" banner. */
import { useState } from "react";
import { api, type SourceHealth, type SourceLoad } from "../../api";
import { notifyChanged, useAsync } from "../../lib";

export const reviewer = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

export const STATE: Record<string, string> = {
  ok: "ok", done: "ok", unchanged: "ok", new: "key", "not loaded": "key", changed: "updating", stale: "", held: "",
  failed: "", unmapped: "",
};

export const cell = (v: unknown) =>
  v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v);
export const when = (t: string | null | undefined) => t?.slice(0, 16).replace("T", " ") ?? "";

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
  if (!h) return null;
  return <span className={`badge ${STATE[h.state] ?? ""}`} title={h.problems.join(", ") || "healthy"}>{h.state}</span>;
}

export function Loaded({ r }: { r: SourceLoad }) {
  if (r.held.length) return <p className="error small">Held: see why at the top of the dataset.</p>;
  if (r.unchanged.length) return <p className="small muted">Unchanged since the last load: nothing to do.</p>;
  return (
    <p className="small">
      Loaded {r.records.toLocaleString()} records ({r.inserted} new, {r.updated} changed, {r.removed} removed).
    </p>
  );
}

export function TestSteps({ steps }: { steps: { step: string; ok: boolean; ms: number; detail: unknown }[] }) {
  return (
    <ul className="small">
      {steps.map((s) => (
        <li key={s.step}><span className={`badge ${s.ok ? "ok" : ""}`}>{s.ok ? "ok" : "failed"}</span> {s.step}{" "}
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
      {!running && <button className="primary" disabled={!!a.busy} onClick={() => a.run("rebuild", () => api.rematch(), () => {
        setTimeout(s.reload, 1500);
      })}>Rebuild now</button>}
      {a.error && <span className="error">{a.error}</span>}
    </div>
  );
}
