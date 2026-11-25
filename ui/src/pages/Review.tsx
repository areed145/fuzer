import { openDocument } from "../DocumentViewer";
import { confirmDialog, Textarea, ErrorText, Line, Text, Checkbox, List, Notice, Field, Tabs } from "../ui";
import { percent } from "../format";
import "../styles/review.css";
import "../styles/trends.css";
import { useEffect, useState, type ReactNode } from "react";
import {
  type HeldLoadPayload,
  type PidConnectionPayload,
  type PidMismatchPayload,
  type RolePayload,
  api,
  type ConflictPayload,
  type CurvePayload,
  type Decision,
  type MappingPayload,
  type MatchPayload,
  type MessagePayload,
  type ReviewItem,
  type ReviewGroup,
  type ReviewKind,
  type SchemaPayload,
  type Stats,
} from "../api";
import { SourceBadge, Status, entityHref, fmt, href, notifyChanged, useAsync, errorMessage } from "../lib";
import { ActionStatus, Bar, Button, ButtonLink, Select, Table, Card as UICard, Input } from "../ui";
import { usePermission } from "../Auth";
import { usePerson } from "../work";
import { personSignedIn } from "../identityStore";
import { FindingEvidence } from "../Finding";
import { say } from "../wording";
import { DrawingViewer } from "../DrawingViewer";
import { MatchRef } from "./masterdata/MatchRef";
import { caseHref } from "./workflows/shared";

/** Where a kind of question is listed: its tab on Master data (P2.3, #505). */
/** The kinds of question, in the order their tabs show. */
const QUESTION_KINDS: [ReviewKind, string][] = [
  ["match", "Possible matches"], ["conflict", "Conflicting values"], ["schema", "Schema changes"], ["mapping", "New tables"],
  ["curve", "Datasheet curves"], ["check", "Held loads"], ["pid_connection", "Drawing connections"],
  ["pid_mismatch", "Drawing disagreements"], ["role", "Measurement roles"], ["message", "Messages"],
];

export const questionsHref = (kind?: string, item?: number) =>
  href("/masterdata", { tab: "questions", kind, item: item === undefined ? undefined : String(item) });

/** What a recorded answer did, in one line; a message's answer links the case it opened (P5.26a). */
function saved(item: ReviewItem, d: Omit<Decision, "by">, opened?: number | null): ReactNode {
  return (
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
                ? `Saved #${item.id}. Recorded: ${mismatchSystem(item.payload)} needs changing to match ${item.payload.sheet}. Nothing was changed in either system.`
                : `Saved #${item.id}. Recorded: ${item.payload.sheet} needs a redline. Nothing was changed in either system.`
          : item.kind === "role"
            ? `Saved #${item.id}. ${item.payload.point ?? "The point"} is ${roleLabel(item.payload, d)} from now on, and stays so after rebuilds.`
          : item.kind === "message"
            ? d.decision === "reject"
              ? "Saved. The message is kept with no case."
              : opened != null
                ? <>Saved. The message is <a href={caseHref(opened)}>case {opened}</a>, routed like any other.</>
                : "Saved. The message is a case about the entity you chose, routed like any other."
          : `Saved #${item.id}. The chosen value is live.`
  );
}

/** The questions the build leaves for the data steward, by kind (P2.3): a tab on Master data. Each is also a case in
 * the steward's queue, which links here opened on its question; answering closes that case. */
export function Review({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const asked = query.get("kind");
  const kind: ReviewKind = asked === "conflict" || asked === "schema" || asked === "mapping" || asked === "curve" ||
    asked === "check" || asked === "pid_connection" || asked === "pid_mismatch" || asked === "role" ||
    asked === "message" ? asked : "match";
  const items = useAsync(() => api.reviews(kind), [kind]);
  const [name, setName] = usePerson();
  const [done, setDone] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<ReactNode>();

  useEffect(() => setDone(new Set()), [kind]);

  const decide = async (item: ReviewItem, d: Omit<Decision, "by">) => {
    const out = await api.decide(item.id, { ...d, by: name || "anonymous" });
    setDone((s) => new Set(s).add(item.id));
    setToast(saved(item, d, out.case));
    notifyChanged();
  };

  const asked_item = Number(query.get("item")) || null;
  // Opened from its case: that question first.
  const open = (items.data?.filter((i) => !done.has(i.id)) ?? [])
    .sort((a, b) => Number(b.id === asked_item) - Number(a.id === asked_item));

  return (
    <section>
      <Line spread>
        <Tabs label="Kinds of question" items={QUESTION_KINDS.map(([k, label]) => ({
          key: k, label: <>{label} <Text muted>{stats?.open_reviews[k] ?? 0}</Text></>, current: kind === k, href: questionsHref(k),
        }))} />
        {/* a signed-in account answers as itself: only a local, unsigned session types a name */}
        {!personSignedIn() && <label className="reviewer">
          Reviewer
          <Input value={name} placeholder="your name" onChange={(e) => setName(e.target.value)} />
        </label>}
      </Line>

      <Text as="p" small>
        {kind === "match"
          ? "These records might be the same entity. Your answer is kept on every rerun."
          : kind === "mapping"
            ? "Check what each column means, edit the mapping if needed, and accept to load the table."
          : kind === "curve"
            ? "Compare Claude's reading with the datasheet. Only accepted curves are used in charts."
          : kind === "schema"
            ? "This dataset's columns changed, so it is on hold. Accept if the change is expected."
          : kind === "pid_connection"
            ? "Check both sheets: accept links the two items, reject leaves them unlinked."
          : kind === "pid_mismatch"
            ? "Check the drawing and the system, then record which one needs changing."
          : kind === "role"
            ? "What each signal means for its equipment. Detectors read roles, never tag names, so confirm or correct the ones Fuzer wasn't sure of."
          : kind === "check"
            ? "The last load was held. Fix it at the source, then load it again from its page."
          : kind === "message"
            ? "Say which entity each message is about, or that it needs no work."
            : "Pick the right value. Your choice wins from now on."}
      </Text>

      {toast && (
        <Notice role="status">
          {toast}
          {/* a message's answer is a case, not a load: Sources and Jobs have nothing to show for it */}
          {kind !== "message" && <p><a href={href("/sources")}>Review source loads and freshness</a>{" · "}
            <a href={href("/jobs")}>Inspect technical execution</a></p>}
        </Notice>
      )}

      <LikeQuestions kind={kind} name={name} onDone={() => { items.reload(); notifyChanged(); }} />
      <Status of={items} empty={items.data && open.length === 0}>
        All done. Nothing left to review here.
      </Status>
      {open.map((item) => <QuestionCard key={item.id} item={item} onDecide={(d) => decide(item, d)} />)}
    </section>
  );
}

/** One question as its card, by kind: the same card on Master data and on the question's case. */
function QuestionCard({ item, onDecide }: { item: ReviewItem; onDecide: OnDecide }) {
  return (
        item.kind === "match" ? (
          <MatchCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "mapping" ? (
          <MappingCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "curve" ? (
          <CurveCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "schema" ? (
          <SchemaCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "check" ? (
          <HeldLoadCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "pid_connection" ? (
          <PidConnectionCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "role" ? (
          <RoleCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "message" ? (
          <MessageCard id={item.id} p={item.payload} onDecide={onDecide} />
        ) : item.kind === "pid_mismatch" ? (
          <PidMismatchCard id={item.id} label={item.label} p={item.payload} onDecide={onDecide} />
        ) : (
          <ConflictCard
            id={item.id}
            label={item.label}
            description={item.description}
            p={item.payload}
            onDecide={onDecide}
          />
        )
  );
}

/** A case's question, answered on the case (#1131): the same card and the same decision as Master data's Questions
 * tab, with the drawing it is about. Nothing shows once it is answered. */
export function CaseQuestion({ id, kind, onDone }: { id: number; kind: ReviewKind; onDone: () => void }) {
  const q = useAsync(() => api.review(kind, id), [kind, id]);
  const [name] = usePerson();
  const [done, setDone] = useState<ReactNode>();
  const item = q.data?.find((i) => i.id === id);
  if (done) return <Notice role="status">{done}</Notice>;
  if (!item) return <Status of={q} />;
  const sheet = item.kind === "pid_mismatch" ? item.payload : null;
  return <div className="case-question">
    <QuestionCard item={item} onDecide={async (d) => {
      const out = await api.decide(item.id, { ...d, by: name || "anonymous" });
      setDone(saved(item, d, out.case)); notifyChanged(); onDone();
    }} />
    {sheet?.document && <DrawingViewer id={sheet.document} highlight={[sheet.fuzer_id]} title={sheet.sheet} compact />}
  </div>;
}

/** Like questions answered once (P2.3c): each group's answers, applied to every question in it; a standing answer
 * also decides new ones like them at each rebuild, as the person who set it. */
function LikeQuestions({ kind, name, onDone }: { kind: ReviewKind; name: string; onDone: () => void }) {
  const groups = useAsync(() => api.reviewGroups(kind), [kind]);
  const operate = usePermission("operate");
  const [standing, setStanding] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string>();
  const [error, setError] = useState<string>();
  if (!groups.data?.length) return result ? <p role="status" className="small">{result}</p> : null;
  const answer = async (g: ReviewGroup, o: ReviewGroup["options"][number]) => {
    const forNew = !!standing[g.key];
    if (!(await confirmDialog(`${o.label} for all ${g.count} questions${forNew ? ", and for new ones like them" : ""}?`))) return;
    setBusy(g.key); setError(undefined);
    try {
      const r = await api.decideGroup({ key: g.key, decision: o.decision, source: o.source, value: o.value, standing: forNew, by: name || "anonymous" });
      setResult(`Answered ${r.decided.length} of ${g.count}${r.skipped.length ? `; ${r.skipped.length} left for you (${r.skipped[0].why})` : ""}.`);
      groups.reload(); onDone();
    } catch (e) { setError(String(errorMessage(e) ?? e)); } finally { setBusy(null); }
  };
  return (
    <UICard className="like-questions" title="Like questions">
      <Table variant="list" density="compact">
        <tbody>{groups.data.map((g) => (
          <tr key={g.key}>
            <th scope="row">{g.count} × {g.label}{g.standing && <Text as="div" small>New ones are answered by {g.standing.set_by}'s standing answer.</Text>}</th>
            <td>{operate && <Line>
              {g.options.map((o) => <Button key={o.label} size="sm" variant="secondary" disabled={!!busy} onClick={() => void answer(g, o)}>{o.label}</Button>)}
              <Checkbox checked={!!standing[g.key]} onChange={(checked) => setStanding({ ...standing, [g.key]: checked })}>and new ones like these</Checkbox>
            </Line>}</td>
          </tr>
        ))}</tbody>
      </Table>
      {result && <p role="status" className="small">{result}</p>}
      <ErrorText>{error}</ErrorText>
    </UICard>
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
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const feedback = <>
    {!operate && <p>Decisions require operate access and any applicable workflow role. Ask an owner to update your roles.</p>}
    {pending && !busy && <Notice role="group" aria-label="Confirm review decision">
      <p>Save this answer for everyone? {consequence} The records catch up in the background after you save.</p>
      <Button type="button" variant="primary" onClick={submit}>Confirm decision</Button>{" "}
      <Button type="button" onClick={() => { setPending(null); setError(undefined); }}>Cancel</Button>
    </Notice>}
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
    <UICard className="review" title="Same entity?" detail={<>#{id}</>} actions={<Score value={p.score} />}>
      <Text as="p" small>{p.why.charAt(0).toUpperCase() + p.why.slice(1)}.{" "}
        Match score {p.score.toFixed(2)}
        {p.review_min != null && p.auto_accept != null
          ? `; scores from ${p.review_min.toFixed(2)} to ${p.auto_accept.toFixed(2)} come here for a person to decide, higher ones match on their own`
          : ""}.</Text>
      <Table variant="compare">
        <thead>
          <tr>
            <th />
            {[p.left, p.right].map((s) => (
              <th key={s.ref}>
                <SourceBadge source={s.source} /> <Text small mono>{s.ref.split(":").slice(2).join(":")}</Text>
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
          Same entity
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "reject" })}>
          Different entities
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
    <UICard className="review" title={<Text mono>{p.dataset}</Text>} detail={<>#{id}</>}
      actions={<Text small muted>
          {s.shape}
          {s.kind ? ` of ${s.kind}` : ""} · key {s.key ? <Text mono>{s.key}</Text> : "none found"} · {p.connector}
        </Text>}>
      <Text as="p" small mono>{p.path}</Text>
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
                  {c.name === s.key && <Text small muted> key</Text>}
                </th>
                <td>
                  {m?.meaning}
                  {m?.ref && <Text small muted> → {m.ref}</Text>}
                  {m?.by === "claude" && <Text small muted> (Claude)</Text>}
                </td>
                <td className="muted small">{m?.why}</td>
                <td className="small">{percent(c.filled)}</td>
                <td className="small mono">{c.samples.slice(0, 3).join(" · ")}</td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      <Textarea
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
    <UICard className="review" title={<><Text mono>{p.dataset}</Text> changed columns</>} detail={<>#{id}</>}
      actions={<Text small muted>{p.connector}</Text>}>
      <Text as="p" small mono>{p.path}</Text>
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
    <UICard className="review" title={<><a href={entityHref(p.fuzer_id)} className="mono"> {label ?? p.fuzer_id} </a>{" "} · {p.field}</>}
      detail={<>#{id}</>}>
      {description && <Text as="p" small>{description}</Text>}
      {p.returned && <p className="small returned">{p.returned.why}</p>}
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
              {current && <Text small muted>current</Text>}
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
        <Button type="button" variant="ghost" disabled={busy} onClick={() => run({ decision: "field" })}>Check it in the field</Button>
        {feedback}
      </form>
    </UICard>
  );
}

function Score({ value }: { value: number }) {
  return (
    <span className="score" title="Match score from 0 (different) to 1 (the same entity)" aria-label={`Match score ${value.toFixed(2)} of 1`}>
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
      <figcaption className="small"><b>{name}</b> <Text muted>({unit}) against {xUnit}</Text></figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name}: ${pts.length} points`}>
        <polyline className="trend-line" fill="none" points={pts.map((p) => `${x(p[0])},${y(p[1])}`).join(" ")} />
        {pts.map((p, i) => <circle key={i} className="trend-dot" cx={x(p[0])} cy={y(p[1])} r={2.5} />)}
      </svg>
      <Text as="div" small muted>{x0}–{x1} {xUnit} · {Math.min(...ys)}–{y1} {unit}</Text>
    </figure>
  );
}

function CurveCard({ id, p, onDecide }: { id: number; p: CurvePayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(p.reading, null, 1));
  const r = p.reading;
  return (
    <UICard className="review" title={<>{p.manufacturer} {p.model}</>} detail={<>#{id}</>}
      actions={<Text small muted>
          {p.doc_number} · read from the {r.read_from === "curve" ? "drawn curve" : r.read_from}
        </Text>}>
      <Text as="p" small>{p.title}{r.notes && ` · ${r.notes}`}</Text>
      <div className="mini-curves">
        {Object.entries(r.series).map(([name, pts]) => (
          <MiniCurve key={name} name={name} unit={r.units[name]} xUnit={r.units.x} pts={pts} />
        ))}
      </div>
      {Object.keys(r.ratings).length > 0 && (
        <Text as="p" small>
          {Object.entries(r.ratings).map(([k, [a, b]]) => `${k.replaceAll("_", " ")} ${a}${b ? ` / ${b}` : ""}`).join(" · ")}
        </Text>
      )}
      {editing && <Textarea className="mono small" rows={14} value={text} onChange={(e) => setText(e.target.value)} />}
      <div className="actions">
        {p.document && <Button onClick={() => openDocument(p.document!)}>Open {p.doc_number}</Button>}
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
  const { busy, feedback, run } = useBusy(onDecide, "Accept adds a piping link between the two entities on the next rebuild.");
  return (
    <UICard className="review" title="Connected across drawings?" detail={<>#{id}</>}>
      <p>{p.question}</p>
      <Text as="p" small>Line {p.line}: {p.sheet} shows it {p.direction === "to" ? "leaving for" : "arriving from"}{" "}
        {p.other_sheet}.</Text>
      <Line>
        <ButtonLink href={href("/", { q: p.sheet })}>Find {p.sheet}</ButtonLink>
        <ButtonLink href={href("/", { q: p.other_sheet })}>Find {p.other_sheet}</ButtonLink>
        <Button variant="primary" disabled={busy || !p.from || !p.to} onClick={() => run({ decision: "accept" })}>Accept</Button>
        <Button variant="secondary" disabled={busy} onClick={() => run({ decision: "reject" })}>Reject</Button>
      </Line>
      {feedback}
    </UICard>
  );
}

const MISMATCH_TITLE: Record<string, string> = { alarm: "Alarm setting differs from the drawing",
  alarm_unset: "Drawing rates an alarm nobody configured", placement: "Instrument drawn on another entity",
  twin: "DEXPI file differs from the scan", flow: "Flow link differs from the drawings" };

/** The side other than the drawing: who changes if the drawing is right. */
const mismatchSystem = (p: PidMismatchPayload) =>
  p.check === "placement" ? "SAP"
    : p.check === "flow" ? (p.systems?.length ? p.systems.map((s) => s === "ec" ? "Energy Components" : s.toUpperCase()).join(" and ") : "the flow network")
    : "the configured alarm";

/** A drawing and the DCS, AF or SAP disagree (#475): which side needs changing. Recording it changes neither. */
function PidMismatchCard({ id, label, p, onDecide }: { id: number; label: string | null; p: PidMismatchPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide,
    "It drafts the correction for the owner of the side that is wrong. Nothing changes there until they approve it.");
  const system = p.check === "alarm" || p.check === "alarm_unset" ? "the alarm" : mismatchSystem(p);
  return (
    <UICard className="review" title={MISMATCH_TITLE[p.check] ?? "Drawing disagreement"} detail={<>#{id}</>}
      actions={<Text small muted>{p.sheet}</Text>}>
      <p>{say(p.question)}</p>
      {p.returned && <p className="small returned">{p.returned.why}</p>}
      {p.check === "flow" ? (
        <Text small mono>
          <a href={entityHref(p.fuzer_id)}>{p.from}</a> → {p.to_id ? <a href={entityHref(p.to_id)}>{p.to}</a> : p.to}
        </Text>
      ) : <a className="mono small" href={entityHref(p.fuzer_id)}>{label ?? p.tag}</a>}
      {p.finding ? <FindingEvidence finding={p.finding} /> : <Text as="div" small>{p.detail}</Text>}
      <Line>
        {p.document
          ? <ButtonLink href={entityHref(p.document)}>Open {p.sheet}</ButtonLink>
          : <ButtonLink href={href("/", { q: p.sheet })}>Find {p.sheet}</ButtonLink>}
        <Button variant="primary" disabled={busy} onClick={() => run({ decision: "choose", value: "drawing" })}>
          Drawing is right, change {system}
        </Button>
        <Button disabled={busy} onClick={() => run({ decision: "choose", value: "system" })}>
          {p.check === "alarm" || p.check === "alarm_unset" ? "Alarm" : system.replace(/^the /, "The ")} is right, redline the drawing
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => run({ decision: "field" })}>Check it in the field</Button>
        <Button variant="ghost" disabled={busy} onClick={() => run({ decision: "reject" })}>Not a disagreement</Button>
      </Line>
      {feedback}
    </UICard>
  );
}

const roleLabel = (p: RolePayload, d: Omit<Decision, "by">) =>
  (d.decision === "accept" ? p.proposed_label : p.choices.find((c) => c.key === d.value)?.label ?? String(d.value))?.toLowerCase();

function RoleCard({ id, p, onDecide }: { id: number; p: RolePayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide, "It sets this point's role only. No values or tags change.");
  const [chosen, setChosen] = useState(p.proposed ?? "");
  return (
    <UICard className="review" title={p.description ?? p.point} detail={<>#{id}</>}
      actions={<Text small muted>{percent(p.confidence)} sure</Text>}>
      <Text as="p" small>
        <Text mono>{p.point}</Text>{p.uom ? ` in ${p.uom}` : ""}
        {p.asset && <> on <a href={entityHref(p.asset)}>{p.asset_label ?? "its equipment"}</a></>}
      </Text>
      <p>{p.proposed_label ? <>Proposed: <b>{p.proposed_label}</b></> : "Nothing says what this point measures."}</p>
      <List small>{p.basis.map((b) => <li key={b}>{b}</li>)}</List>
      <Line>
        {p.proposed && <Button variant="primary" disabled={busy} onClick={() => run({ decision: "accept" })}>
          Confirm {p.proposed_label?.toLowerCase()}
        </Button>}
        <Field inline label="Or"><Select aria-label="Role" value={chosen} onChange={(e) => setChosen(e.target.value)}>
            <option value="">pick a role</option>
            {p.choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </Select></Field>
        <Button disabled={busy || !chosen || chosen === p.proposed}
          onClick={() => run({ decision: "choose", value: chosen })}>Use this role</Button>
      </Line>
      {feedback}
    </UICard>
  );
}

/** A message to the IOC that named no entity for certain (P2.8): the entity it is about opens its case. */
function MessageCard({ id, p, onDecide }: { id: number; p: MessagePayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide, "The entity you choose gets a case, routed like any other.");
  const m = p.message;
  return (
    <UICard className="review" title={`${m.channel_label} from ${m.sender}`} detail={<>#{id}</>}>
      {m.subject && <Text as="p" small>{m.subject}</Text>}
      <p>{m.text}</p>
      <Text as="p" small>Why it stopped: {p.why}</Text>
      <Line wrap>
        {p.candidates.map((c) => <Button key={c.fuzer_id} disabled={busy}
          onClick={() => run({ decision: "choose", value: c.fuzer_id })}>About {c.label ?? c.fuzer_id}</Button>)}
        <MatchRef value={p.candidates[0]?.label ?? ""} label="Another entity" canRemember={false} busy={busy}
          onPick={(fid) => run({ decision: "choose", value: fid })} />
        <Button variant="secondary" disabled={busy} onClick={() => run({ decision: "reject" })}>No work needed</Button>
      </Line>
      {feedback}
    </UICard>
  );
}

function HeldLoadCard({ id, p, onDecide }: { id: number; p: HeldLoadPayload; onDecide: OnDecide }) {
  const { busy, feedback, run } = useBusy(onDecide,
    "Seen acknowledges this notice only. It does not fix the source, change the checks or retry ingestion.");
  const failed = p.failed ?? [];
  return (
    <UICard title={<>Held load <code>{p.dataset}</code></>} detail={<>#{id}</>}>
      <List small>
        {failed.map((f, i) => (
          <li key={i}>{f.kind === "duplicate keys" ? `repeats ${f.value} key${f.value === 1 ? "" : "s"}` : `${f.kind} failed (${f.value})`}
            {f.detail?.examples?.length ? <Text muted> e.g. {f.detail.examples.join(", ")}</Text> : null}</li>
        ))}
      </List>
      <Line>
        <ButtonLink variant="primary" href={href("/sources", { view: "dataset", name: p.dataset })}>Open the dataset</ButtonLink>
        <Button variant="secondary" disabled={busy} onClick={() => run({ decision: "accept" })}>Seen</Button>
      </Line>
      {feedback}
    </UICard>
  );
}
