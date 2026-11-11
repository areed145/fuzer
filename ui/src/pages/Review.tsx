import { projectUrl } from "../authState";
import "../styles/review.css";
import "../styles/trends.css";
import { useEffect, useState } from "react";
import {
  type HeldLoadPayload,
  type PidConnectionPayload,
  type PidMismatchPayload,
  api,
  type ConflictPayload,
  type CurvePayload,
  type Decision,
  type MappingPayload,
  type MatchPayload,
  type ReviewItem,
  type ReviewKind,
  type SchemaPayload,
  type Stats,
} from "../api";
import { SourceBadge, Status, entityHref, fmt, href, notifyChanged, useAsync } from "../lib";
import { ActionStatus, Bar, Button, ButtonLink, PageHeader, Table, Card as UICard, Input } from "../ui";
import { usePermission } from "../Auth";
import { usePerson } from "../work";

export function Review({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const asked = query.get("kind");
  const kind: ReviewKind = asked === "conflict" || asked === "schema" || asked === "mapping" || asked === "curve" ||
    asked === "check" || asked === "pid_connection" || asked === "pid_mismatch" ? asked : "match";
  const items = useAsync(() => api.reviews(kind), [kind]);
  const [name, setName] = usePerson();
  const [done, setDone] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string>();

  useEffect(() => setDone(new Set()), [kind]);

  const decide = async (item: ReviewItem, d: Omit<Decision, "by">) => {
    await api.decide(item.id, { ...d, by: name || "anonymous" });
    setDone((s) => new Set(s).add(item.id));
    setToast(
      item.kind === "match"
        ? `Saved #${item.id}. Fuzer is updating the records in the background.`
        : item.kind === "mapping"
          ? d.decision === "reject"
            ? `Saved #${item.id}. ${item.payload.dataset} stays unmapped.`
            : `Saved #${item.id}. ${item.payload.dataset} loads with this mapping on the next ingest.`
        : item.kind === "curve"
          ? d.decision === "reject"
            ? `Saved #${item.id}. ${item.payload.doc_number} is not used.`
            : `Saved #${item.id}. The ${item.payload.model} curve is used from the next rebuild (running now).`
        : item.kind === "schema"
          ? d.decision === "accept"
            ? `Saved #${item.id}. ${item.payload.dataset} loads with its new columns on the next ingest.`
            : `Saved #${item.id}. ${item.payload.dataset} stays on hold.`
          : item.kind === "check"
            ? `Saved #${item.id}. Load ${item.payload.dataset} again once it's fixed.`
          : item.kind === "pid_connection"
            ? d.decision === "accept"
              ? `Saved #${item.id}. ${item.payload.from} pipes to ${item.payload.to} from the next rebuild (running now).`
              : `Saved #${item.id}. The line stays unlinked.`
          : item.kind === "pid_mismatch"
            ? d.decision === "reject"
              ? `Saved #${item.id}. Recorded as not a disagreement.`
              : d.value === "drawing"
                ? `Saved #${item.id}. Recorded: ${item.payload.check === "placement" ? "SAP" : "the configured alarm"} needs changing to match ${item.payload.sheet}. Nothing was changed in either system.`
                : `Saved #${item.id}. Recorded: ${item.payload.sheet} needs a redline. Nothing was changed in either system.`
          : `Saved #${item.id}. The chosen value is live.`,
    );
    notifyChanged();
  };

  const open = items.data?.filter((i) => !done.has(i.id)) ?? [];

  return (
    <section>
      <PageHeader subtitle={<>
        Register data waiting for a person's decision: records that may be the same thing, sources that disagree on a
        value, and tables whose columns changed. Each decision is kept and applied on the next rebuild. Workflow steps
        waiting on you are in your <a href={href("/inbox")}>Inbox</a>.
        {" "}Connection, ingestion and freshness recovery belong in <a href={href("/sources")}>Sources</a>.
      </>}>Data review</PageHeader>
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
          <a className={kind === "mapping" ? "on" : ""} href={href("/review", { kind: "mapping" })}>
            New tables <span className="muted">{stats?.open_reviews.mapping ?? 0}</span>
          </a>
          <a className={kind === "curve" ? "on" : ""} href={href("/review", { kind: "curve" })}>
            Datasheet curves <span className="muted">{stats?.open_reviews.curve ?? 0}</span>
          </a>
          <a className={kind === "check" ? "on" : ""} href={href("/review", { kind: "check" })}>
            Held loads <span className="muted">{stats?.open_reviews.check ?? 0}</span>
          </a>
          <a className={kind === "pid_connection" ? "on" : ""} href={href("/review", { kind: "pid_connection" })}>
            Drawing connections <span className="muted">{stats?.open_reviews.pid_connection ?? 0}</span>
          </a>
          <a className={kind === "pid_mismatch" ? "on" : ""} href={href("/review", { kind: "pid_mismatch" })}>
            Drawing disagreements <span className="muted">{stats?.open_reviews.pid_mismatch ?? 0}</span>
          </a>
        </div>
        <label className="reviewer">
          Reviewer
          <Input value={name} placeholder="your name" onChange={(e) => setName(e.target.value)} />
        </label>
      </div>

      <p className="muted">
        {kind === "match"
          ? "Fuzer thinks these records might be the same physical item, but isn't sure. Your answer is saved and kept on every rerun."
          : kind === "mapping"
            ? "Tables Fuzer has no mapping for. It guessed what each column means; check the meanings, edit the mapping if needed, and accept to load the table."
          : kind === "curve"
            ? "Curves Claude read off OEM datasheets. Compare its reading with the sheet; accept, correct, or reject. Only accepted curves are used in charts."
          : kind === "schema"
            ? "A dataset lost or changed columns since it was last loaded, so it is on hold: its records stay as they were. Accept if the change is expected."
          : kind === "pid_connection"
            ? "Lines that leave one P&ID for another, where the other drawing doesn't show them. Check both sheets: accept links the two items, reject leaves them unlinked."
          : kind === "pid_mismatch"
            ? "Places where a P&ID and another system disagree: an alarm or trip setting that differs from the DCS or AF, or an instrument SAP files under a different item. Check the drawing and the system, then record which one needs changing. Recording a decision changes neither."
          : kind === "check"
            ? "A dataset's last load was held: it repeated keys or failed a check that holds the load, so its records stay as they were. Fix it at the source (or the check), then load it again from its page."
            : "Sources disagree on a value. Pick the right one; your choice always wins from now on."}
      </p>

      {toast && (
        <div role="status" className="notice">
          {toast}
          <p><a href={href("/sources")}>Review source loads and freshness</a>{" · "}
            <a href={href("/jobs")}>Inspect technical execution</a></p>
        </div>
      )}

      <Status loading={items.loading && !items.data} error={items.error} empty={items.data && open.length === 0}>
        All done. Nothing left to review here.
      </Status>
      {open.map((item) =>
        item.kind === "match" ? (
          <MatchCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "mapping" ? (
          <MappingCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "curve" ? (
          <CurveCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "schema" ? (
          <SchemaCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "check" ? (
          <HeldLoadCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "pid_connection" ? (
          <PidConnectionCard key={item.id} id={item.id} p={item.payload} onDecide={(d) => decide(item, d)} />
        ) : item.kind === "pid_mismatch" ? (
          <PidMismatchCard key={item.id} id={item.id} label={item.label} p={item.payload} onDecide={(d) => decide(item, d)} />
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

function useBusy(onDecide: OnDecide, consequence = "It may change accepted values, asset links or future loads.") {
  const operate = usePermission("operate");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState<Omit<Decision, "by"> | null>(null);
  const submit = async () => {
    if (!pending || busy || !operate) return;
    setBusy(true);
    setError(undefined);
    try {
      await onDecide(pending);
      setPending(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const feedback = <>
    {!operate && <p>Decisions require operate access and any applicable workflow role. Ask an owner to update your roles.</p>}
    {pending && !busy && <div className="notice" role="group" aria-label="Confirm review decision">
      <p>Record this shared {pending.decision} decision? {consequence}
        A review decision is not proof that ingestion or a background rebuild has completed.</p>
      <Button type="button" variant="primary" onClick={submit}>Confirm decision</Button>{" "}
      <Button type="button" onClick={() => { setPending(null); setError(undefined); }}>Cancel</Button>
    </div>}
    <ActionStatus busy={busy ? "Saving review decision…" : null} error={error} />
  </>;
  return { busy: busy || !operate, feedback, run: (d: Omit<Decision, "by">) => {
    if (!busy && operate) { setError(undefined); setPending(d); }
  } };
}

function MatchCard({ id, p, onDecide }: { id: number; p: MatchPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide);
  const fields = [...new Set([...Object.keys(p.left.fields), ...Object.keys(p.right.fields)])].sort();
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>Same item? <span className="muted small">#{id}</span></h3>
        <Score value={p.score} />
      </div>
      <p className="muted small">{p.why.charAt(0).toUpperCase() + p.why.slice(1)}.{" "}
        Match score {p.score.toFixed(2)}
        {p.review_min != null && p.auto_accept != null
          ? `; scores from ${p.review_min.toFixed(2)} to ${p.auto_accept.toFixed(2)} come here for a person to decide, higher ones match on their own`
          : ""}.</p>
      <Table variant="compare">
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
      </Table>
      <div className="actions">
        <Button variant="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>
          Same item
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Different items
        </Button>
        {feedback}
      </div>
    </UICard>
  );
}

function MappingCard({ id, p, onDecide }: { id: number; p: MappingPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide);
  const [text, setText] = useState(p.yaml);
  const s = p.suggestion;
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>
          <span className="mono">{p.dataset}</span> <span className="muted small">#{id}</span>
        </h3>
        <span className="muted small">
          {s.shape}
          {s.kind ? ` of ${s.kind}` : ""} · key {s.key ? <span className="mono">{s.key}</span> : "none found"} · {p.connector}
        </span>
      </div>
      <p className="muted small mono">{p.path}</p>
      <Table variant="compare">
        <thead>
          <tr>
            <th>column</th>
            <th>meaning</th>
            <th>why</th>
            <th>filled</th>
            <th>samples</th>
          </tr>
        </thead>
        <tbody>
          {p.profile.map((c) => {
            const m = s.columns[c.name];
            return (
              <tr key={c.name} className={m?.meaning === "other" ? "diff" : ""}>
                <th className="mono">
                  {c.name}
                  {c.name === s.key && <span className="muted small"> key</span>}
                </th>
                <td>
                  {m?.meaning}
                  {m?.ref && <span className="muted small"> → {m.ref}</span>}
                  {m?.by === "claude" && <span className="muted small"> (Claude)</span>}
                </td>
                <td className="muted small">{m?.why}</td>
                <td className="small">{Math.round(c.filled * 100)}%</td>
                <td className="small mono">{c.samples.slice(0, 3).join(" · ")}</td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      <textarea
        className="mono yaml"
        spellCheck={false}
        rows={Math.min(30, text.split("\n").length + 1)}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="actions">
        <Button
          variant="primary"
          disabled={busy}
          onClick={() => run(text === p.yaml ? { decision: "accept" } : { decision: "choose", value: text })}
        >
          {text === p.yaml ? "Accept mapping" : "Save edited mapping"}
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Not now
        </Button>
        {feedback}
      </div>
    </UICard>
  );
}

function SchemaCard({ id, p, onDecide }: { id: number; p: SchemaPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide);
  const was = new Map(p.old);
  const now = new Map(p.new);
  const columns = [...new Set([...was.keys(), ...now.keys()])];
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>
          <span className="mono">{p.dataset}</span> changed columns <span className="muted small">#{id}</span>
        </h3>
        <span className="muted small">{p.connector}</span>
      </div>
      <p className="muted small mono">{p.path}</p>
      <Table variant="compare">
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
      </Table>
      <div className="actions">
        <Button variant="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>
          Expected, load it
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Keep on hold
        </Button>
        {feedback}
      </div>
    </UICard>
  );
}

function ConflictCard({ id, label, description, p, onDecide }: {
  id: number;
  label: string | null;
  description: string | null;
  p: ConflictPayload;
  onDecide: OnDecide;
}) {
  const { busy, feedback, run } = useBusy(onDecide);
  const [custom, setCustom] = useState("");
  const distinct = [...new Map(p.values.map((v) => [fmt(v.value), v])).values()];
  return (
    <UICard className="review">
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
              <Button
                disabled={busy}
                className={current ? "primary" : ""}
                onClick={() => run(current ? { decision: "accept" } : { decision: "choose", value: v.value })}
              >
                Use this
              </Button>
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
        <Input value={custom} placeholder="or type the correct value" onChange={(e) => setCustom(e.target.value)} />
        <Button disabled={busy || !custom.trim()}>Save</Button>
        {feedback}
      </form>
    </UICard>
  );
}

function Score({ value }: { value: number }) {
  return (
    <span className="score" title="Match score from 0 (different) to 1 (the same item)" aria-label={`Match score ${value.toFixed(2)} of 1`}>
      <Bar className="bar" value={Math.round(value * 100)} />
      {value.toFixed(2)}
    </span>
  );
}

/** One series as a small line chart (points marked), for checking against the sheet. */
function MiniCurve({ name, unit, xUnit, pts }: { name: string; unit?: string; xUnit?: string; pts: [number, number][] }) {
  const W = 260, H = 120;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(0, ...ys), Math.max(...ys)];
  const x = (v: number) => 4 + ((v - x0) / (x1 - x0 || 1)) * (W - 8);
  const y = (v: number) => H - 4 - ((v - y0) / (y1 - y0 || 1)) * (H - 8);
  return (
    <figure className="mini-curve">
      <figcaption className="small"><b>{name}</b> <span className="muted">({unit}) against {xUnit}</span></figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name}: ${pts.length} points`}>
        <polyline className="trend-line" fill="none" points={pts.map((p) => `${x(p[0])},${y(p[1])}`).join(" ")} />
        {pts.map((p, i) => <circle key={i} className="trend-dot" cx={x(p[0])} cy={y(p[1])} r={2.5} />)}
      </svg>
      <div className="muted small">{x0}–{x1} {xUnit} · {Math.min(...ys)}–{y1} {unit}</div>
    </figure>
  );
}

function CurveCard({ id, p, onDecide }: { id: number; p: CurvePayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(p.reading, null, 1));
  const r = p.reading;
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>
          {p.manufacturer} {p.model} <span className="muted small">#{id}</span>
        </h3>
        <span className="muted small">
          {p.document ? <a href={projectUrl(`/documents/${encodeURIComponent(p.document)}/file`)} target="_blank" rel="noreferrer">
            {p.doc_number}</a> : p.doc_number} · read from the {r.read_from === "curve" ? "drawn curve" : r.read_from}
        </span>
      </div>
      <p className="muted small">{p.title}{r.notes && ` · ${r.notes}`}</p>
      <div className="mini-curves">
        {Object.entries(r.series).map(([name, pts]) => (
          <MiniCurve key={name} name={name} unit={r.units[name]} xUnit={r.units.x} pts={pts} />
        ))}
      </div>
      {Object.keys(r.ratings).length > 0 && (
        <p className="small">
          {Object.entries(r.ratings).map(([k, [a, b]]) => `${k.replaceAll("_", " ")} ${a}${b ? ` / ${b}` : ""}`).join(" · ")}
        </p>
      )}
      {editing && <textarea className="mono small" rows={14} value={text} onChange={(e) => setText(e.target.value)} />}
      <div className="actions">
        {editing ? (
          <Button variant="primary" disabled={busy} onClick={() => {
            try {
              run({ decision: "choose", value: JSON.parse(text) });
            } catch {
              run({ decision: "choose", value: "not json" });
            }
          }}>Save corrected curve</Button>
        ) : (
          <Button variant="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>Matches the sheet, use it</Button>
        )}
        <Button disabled={busy} onClick={() => setEditing((e) => !e)}>{editing ? "Cancel" : "Correct values"}</Button>
        <Button disabled={busy} onClick={() => run({ decision: "reject" })}>Wrong, don't use</Button>
        {feedback}
      </div>
    </UICard>
  );
}


/** A load held by its checks (#225): why, and the dataset to fix it on. */
function PidConnectionCard({ id, p, onDecide }: { id: number; p: PidConnectionPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide, "Accept adds a piping link between the two items on the next rebuild.");
  return (
    <UICard className="review">
      <h3>Connected across drawings? <span className="muted small">#{id}</span></h3>
      <p>{p.question}</p>
      <p className="muted small">Line {p.line}: {p.sheet} shows it {p.direction === "to" ? "leaving for" : "arriving from"}{" "}
        {p.other_sheet}.</p>
      <div className="row">
        <ButtonLink href={href("/", { q: p.sheet })}>Find {p.sheet}</ButtonLink>
        <ButtonLink href={href("/", { q: p.other_sheet })}>Find {p.other_sheet}</ButtonLink>
        <Button variant="primary" disabled={busy || !p.from || !p.to} onClick={() => run({ decision: "accept" })}>Accept</Button>
        <Button variant="secondary" disabled={busy} onClick={() => run({ decision: "reject" })}>Reject</Button>
      </div>
      {feedback}
    </UICard>
  );
}

const MISMATCH_TITLE = { alarm: "Alarm setting differs from the drawing", alarm_unset: "Drawing rates an alarm nobody configured",
  placement: "Instrument drawn on another item" } as const;

/** A drawing and the DCS, AF or SAP disagree (#475): which side needs changing. Recording it changes neither. */
function PidMismatchCard({ id, label, p, onDecide }: { id: number; label: string | null; p: PidMismatchPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide,
    "It records which side needs changing for the team to act on. It does not change the drawing, the DCS, AF or SAP.");
  const system = p.check === "placement" ? "SAP" : "the alarm";
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>{MISMATCH_TITLE[p.check] ?? "Drawing disagreement"} <span className="muted small">#{id}</span></h3>
        <span className="muted small">{p.sheet}</span>
      </div>
      <p>{p.question}</p>
      <p className="muted small">
        <a className="mono" href={entityHref(p.fuzer_id)}>{label ?? p.tag}</a>
        {p.check === "placement" && p.register
          ? <> · drawn on <span className="mono">{p.drawn_on}</span>; {p.register.map((r) => `${r.sources.join("/")} has it under ${r.parent}`).join(", ")}</>
          : <> · {p.detail}</>}
      </p>
      <div className="row">
        {p.document
          ? <ButtonLink href={entityHref(p.document)}>Open {p.sheet}</ButtonLink>
          : <ButtonLink href={href("/", { q: p.sheet })}>Find {p.sheet}</ButtonLink>}
        <Button variant="primary" disabled={busy} onClick={() => run({ decision: "choose", value: "drawing" })}>
          Drawing is right, change {system}
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "choose", value: "system" })}>
          {p.check === "placement" ? "SAP" : "Alarm"} is right, redline the drawing
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => run({ decision: "reject" })}>Not a disagreement</Button>
      </div>
      {feedback}
    </UICard>
  );
}

function HeldLoadCard({ id, p, onDecide }: { id: number; p: HeldLoadPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide,
    "Seen acknowledges this notice only. It does not fix the source, change the checks or retry ingestion.");
  const failed = p.failed ?? [];
  return (
    <UICard className="">
      <h2>#{id} <code>{p.dataset}</code> <span className="muted small">held load</span></h2>
      <ul className="small">
        {failed.map((f, i) => (
          <li key={i}>{f.kind === "duplicate keys" ? `repeats ${f.value} key${f.value === 1 ? "" : "s"}` : `${f.kind} failed (${f.value})`}
            {f.detail?.examples?.length ? <span className="muted"> e.g. {f.detail.examples.join(", ")}</span> : null}</li>
        ))}
      </ul>
      <div className="row">
        <ButtonLink variant="primary" href={href("/sources", { view: "dataset", name: p.dataset })}>Open the dataset</ButtonLink>
        <Button variant="secondary" disabled={busy} onClick={() => run({ decision: "accept" })}>Seen</Button>
      </div>
      {feedback}
    </UICard>
  );
}
