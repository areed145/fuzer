import { useEffect, useState } from "react";
import {
  api,
  type ConflictPayload,
  type Decision,
  type MatchPayload,
  type ReviewItem,
  type ReviewKind,
  type SchemaPayload,
  type Stats,
} from "../api";
import { SourceBadge, Status, entityHref, fmt, href, notifyChanged, useAsync } from "../lib";

const NAME_KEY = "fuzer.reviewer";

function loadName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function Review({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const asked = query.get("kind");
  const kind: ReviewKind = asked === "conflict" || asked === "schema" ? asked : "match";
  const items = useAsync(() => api.reviews(kind), [kind]);
  const [name, setName] = useState(loadName);
  const [done, setDone] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string>();

  useEffect(() => {
    try {
      localStorage.setItem(NAME_KEY, name);
    } catch {
      /* private mode: name just isn't remembered */
    }
  }, [name]);

  useEffect(() => setDone(new Set()), [kind]);

  const decide = async (item: ReviewItem, d: Omit<Decision, "by">) => {
    await api.decide(item.id, { ...d, by: name || "anonymous" });
    setDone((s) => new Set(s).add(item.id));
    setToast(
      item.kind === "match"
        ? `Saved #${item.id}. Fuzer is updating the records in the background.`
        : item.kind === "schema"
          ? d.decision === "accept"
            ? `Saved #${item.id}. ${item.payload.dataset} loads with its new columns on the next ingest.`
            : `Saved #${item.id}. ${item.payload.dataset} stays on hold.`
          : `Saved #${item.id}. The chosen value is live.`,
    );
    notifyChanged();
  };

  const open = items.data?.filter((i) => !done.has(i.id)) ?? [];

  return (
    <section>
      <div className="row spread">
        <div className="tabs">
          <a className={kind === "match" ? "on" : ""} href={href("/review", { kind: "match" })}>
            Possible matches <span className="muted">{stats?.open_reviews.match ?? 0}</span>
          </a>
          <a className={kind === "conflict" ? "on" : ""} href={href("/review", { kind: "conflict" })}>
            Conflicting values <span className="muted">{stats?.open_reviews.conflict ?? 0}</span>
          </a>
          <a className={kind === "schema" ? "on" : ""} href={href("/review", { kind: "schema" })}>
            Schema changes <span className="muted">{stats?.open_reviews.schema ?? 0}</span>
          </a>
        </div>
        <label className="reviewer">
          Reviewer
          <input value={name} placeholder="your name" onChange={(e) => setName(e.target.value)} />
        </label>
      </div>

      <p className="muted">
        {kind === "match"
          ? "Fuzer thinks these records might be the same physical item, but isn't sure. Your answer is saved and kept on every rerun."
          : kind === "schema"
            ? "A dataset lost or changed columns since it was last loaded, so it is on hold: its records stay as they were. Accept if the change is expected."
            : "Sources disagree on a value. Pick the right one; your choice always wins from now on."}
      </p>

      {toast && (
        <div className="toast" onAnimationEnd={() => setToast(undefined)}>
          {toast}
        </div>
      )}

      <Status loading={items.loading && !items.data} error={items.error} empty={items.data && open.length === 0}>
        All done. Nothing left to review here.
      </Status>
      {open.map((item) =>
        item.kind === "match" ? (
          <MatchCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "schema" ? (
          <SchemaCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : (
          <ConflictCard
            key={item.id}
            id={item.id}
            label={item.label}
            description={item.description}
            p={item.payload}
            onDecide={(d) => decide(item, d)}
          />
        ),
      )}
    </section>
  );
}

type OnDecide = (d: Omit<Decision, "by">) => Promise<void>;

function useBusy(onDecide: OnDecide) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async (d: Omit<Decision, "by">) => {
    setBusy(true);
    setError(undefined);
    try {
      await onDecide(d);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function MatchCard({ id, p, onDecide }: { id: number; p: MatchPayload; onDecide: OnDecide }) {
  const { busy, error, run } = useBusy(onDecide);
  const fields = [...new Set([...Object.keys(p.left.fields), ...Object.keys(p.right.fields)])].sort();
  return (
    <div className="card review">
      <div className="row spread">
        <h3>Same item? <span className="muted small">#{id}</span></h3>
        <Score value={p.score} />
      </div>
      <p className="muted small">{p.why}</p>
      <table className="compare">
        <thead>
          <tr>
            <th />
            {[p.left, p.right].map((s) => (
              <th key={s.ref}>
                <SourceBadge source={s.source} /> <span className="mono small">{s.ref.split(":").slice(2).join(":")}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => {
            const l = fmt(p.left.fields[f]);
            const r = fmt(p.right.fields[f]);
            return (
              <tr key={f} className={l !== r ? "diff" : ""}>
                <th>{f}</th>
                <td>{l}</td>
                <td>{r}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>
          Same item
        </button>
        <button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Different items
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}

function SchemaCard({ id, p, onDecide }: { id: number; p: SchemaPayload; onDecide: OnDecide }) {
  const { busy, error, run } = useBusy(onDecide);
  const was = new Map(p.old);
  const now = new Map(p.new);
  const columns = [...new Set([...was.keys(), ...now.keys()])];
  return (
    <div className="card review">
      <div className="row spread">
        <h3>
          <span className="mono">{p.dataset}</span> changed columns <span className="muted small">#{id}</span>
        </h3>
        <span className="muted small">{p.connector}</span>
      </div>
      <p className="muted small mono">{p.path}</p>
      <table className="compare">
        <thead>
          <tr>
            <th>column</th>
            <th>last loaded</th>
            <th>now</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c} className={was.get(c) !== now.get(c) ? "diff" : ""}>
              <th className="mono">{c}</th>
              <td>{was.get(c) ?? "(new)"}</td>
              <td>{now.get(c) ?? "(removed)"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>
          Expected, load it
        </button>
        <button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Keep on hold
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}

function ConflictCard({ id, label, description, p, onDecide }: {
  id: number;
  label: string | null;
  description: string | null;
  p: ConflictPayload;
  onDecide: OnDecide;
}) {
  const { busy, error, run } = useBusy(onDecide);
  const [custom, setCustom] = useState("");
  const distinct = [...new Map(p.values.map((v) => [fmt(v.value), v])).values()];
  return (
    <div className="card review">
      <div className="row spread">
        <h3>
          <a href={entityHref(p.fuzer_id)} className="mono">
            {label ?? p.fuzer_id}
          </a>{" "}
          · {p.field} <span className="muted small">#{id}</span>
        </h3>
      </div>
      {description && <p className="muted small">{description}</p>}
      <ul className="choices">
        {distinct.map((v) => {
          const from = p.values.filter((x) => fmt(x.value) === fmt(v.value));
          const current = fmt(v.value) === fmt(p.chosen);
          return (
            <li key={fmt(v.value)} className={current ? "current" : ""}>
              <span className="value">{fmt(v.value)}</span>
              {from.map((x) => (
                <SourceBadge key={`${x.source}:${x.source_key}`} source={x.source} />
              ))}
              {current && <span className="muted small">current</span>}
              <button
                disabled={busy}
                className={current ? "primary" : ""}
                onClick={() => run(current ? { decision: "accept" } : { decision: "choose", value: v.value })}
              >
                Use this
              </button>
            </li>
          );
        })}
      </ul>
      <form
        className="actions"
        onSubmit={(e) => {
          e.preventDefault();
          if (custom.trim()) run({ decision: "choose", value: custom.trim() });
        }}
      >
        <input value={custom} placeholder="or type the correct value" onChange={(e) => setCustom(e.target.value)} />
        <button disabled={busy || !custom.trim()}>Save</button>
        {error && <span className="error">{error}</span>}
      </form>
    </div>
  );
}

function Score({ value }: { value: number }) {
  return (
    <span className="score" title="Match score (0–1)">
      <span className="bar">
        <span style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      {value.toFixed(2)}
    </span>
  );
}
