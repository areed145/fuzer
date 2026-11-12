/** Structured close-out (P6.1a, #655): closing a case says what it was and, for a real problem, what failed, why and
 * what was done, from the ISO 14224 lists. The form comes pre-filled from the case's own words or an agent's
 * suggestion, and the person confirms it. On a phone it is a handful of selects, one per line. */
import { useState } from "react";
import { api, type CaseCloseout, type CloseoutVocabulary } from "../api";
import { Button, Card, Field, Input, Select } from "../ui";
import { Status, useAsync } from "../lib";
import { Actor, when } from "../work";
import { caseHref, useAct } from "./workflows/shared";

type Draft = Partial<Record<"disposition" | "failure_mode" | "mechanism" | "cause" | "activity" | "outcome" | "note", string>>
  & { duplicate_of?: string };

const pick = (list: Record<string, string>, code?: string | null) => (code ? list[code] ?? code : "");

/** One line for a recorded close-out, as the case's history reads it. */
export function closeoutLine(r: CaseCloseout, v: CloseoutVocabulary): string {
  const what = v.dispositions[r.disposition] ?? r.disposition;
  if (r.disposition === "duplicate") return `${what} of case ${r.duplicate_of}`;
  if (r.disposition !== "real") return what;
  return `${what}: ${pick(v.failure_modes, r.failure_mode)}, cause ${pick(v.causes, r.cause).toLowerCase()}, `
    + `${pick(v.activities, r.activity).toLowerCase()}${r.outcome ? `; ${pick(v.outcomes, r.outcome).toLowerCase()}` : ""}`;
}

export function CaseCloseoutCard({ itemId, person, closed, readOnly, reload }:
  { itemId: number; person: string; closed: boolean; readOnly: boolean; reload: () => void }) {
  const form = useAsync(() => api.caseCloseout(itemId), [itemId]);
  const [open, setOpen] = useState(false);
  if (!form.data) return form.error ? <Status error={form.error} onRetry={form.reload} /> : null;
  const { record, vocabulary: v } = form.data;
  if (record && !open) {
    return (
      <Card className="closeout-done" title="Close-out">
        <p className="small">{closeoutLine(record, v)}{record.note && <span className="muted"> · {record.note}</span>}</p>
        <p className="small muted">by <Actor who={record.by} /> {when(record.at)}
          {!readOnly && <> · <Button variant="link" onClick={() => setOpen(true)}>Correct it</Button></>}</p>
        {record.held != null
          ? <p className="small">{record.held ? "The fix held" : "The fix did not hold"}, said <Actor who={record.held_by ?? ""} />
            {record.held_note && <span className="muted">: {record.held_note}</span>}</p>
          : record.recheck_at && <p className="small muted">Re-check {when(record.recheck_at)}: did the fix hold?</p>}
        {(form.data.repeats ?? []).length > 0 && <p className="small notice">Came back:{" "}
          {(form.data.repeats ?? []).map((r, i) => <span key={r.again}>{i > 0 && ", "}<a href={caseHref(r.again)}>case {r.again}</a></span>)}</p>}
      </Card>
    );
  }
  if (readOnly) return closed ? <p className="small muted">Close-out missing.</p> : null;
  if (!closed && !open) return <Button variant="primary" onClick={() => setOpen(true)}>Close out</Button>;
  return <CloseoutForm itemId={itemId} person={person} data={form.data} missing={closed && !record}
    onDone={() => { setOpen(false); form.reload(); reload(); }} onCancel={closed && !record ? undefined : () => setOpen(false)} />;
}

function CloseoutForm({ itemId, person, data, missing, onDone, onCancel }: {
  itemId: number; person: string; data: NonNullable<Awaited<ReturnType<typeof api.caseCloseout>>>; missing: boolean;
  onDone: () => void; onCancel?: () => void;
}) {
  const v = data.vocabulary;
  const s = data.suggestion;
  const start: Draft = data.record
    ? Object.fromEntries(Object.entries(data.record).filter(([, x]) => x != null).map(([k, x]) => [k, String(x)]))
    : { disposition: s.disposition, failure_mode: s.failure_mode, mechanism: s.mechanism, cause: s.cause,
        activity: s.activity, outcome: s.outcome };
  const [d, setD] = useState<Draft>(start);
  const a = useAct();
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD({ ...d, [k]: e.target.value || undefined });
  const real = d.disposition === "real";
  const ready = !!d.disposition && (!real || (!!d.failure_mode && !!d.cause && !!d.activity))
    && (d.disposition !== "duplicate" || !!Number(d.duplicate_of));
  const suggested = !data.record && (s.disposition || s.failure_mode || s.activity);
  const list = (k: keyof Draft, label: string, options: Record<string, string>, required = false) => (
    <Field label={label} width="full">
      <Select width="full" aria-label={label} value={d[k] ?? ""} onChange={set(k)} required={required}>
        <option value="">{required ? "Choose" : "Not known"}</option>
        {Object.entries(options).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </Select>
    </Field>
  );
  const save = () => a.run("closeout", () => api.recordCaseCloseout(itemId, {
    by: person, disposition: d.disposition!, failure_mode: d.failure_mode, mechanism: d.mechanism, cause: d.cause,
    activity: d.activity, outcome: d.outcome, note: d.note, duplicate_of: d.duplicate_of ? Number(d.duplicate_of) : undefined,
    suggested: data.record ? undefined : s,
  }), onDone);
  return (
    <Card className="closeout-form" title={missing ? "Close-out missing" : "Close out"}>
      {missing && <p className="small">This case closed without saying what it was. Fill it in so it counts.</p>}
      {suggested && <p className="small muted">Filled in from {s.from === "agent" ? "the agent's suggestion" : "what the case says"}.
        Check it before you save.</p>}
      {list("disposition", "What it was", v.dispositions, true)}
      {real && <>
        {list("failure_mode", "Failure mode", v.failure_modes, true)}
        {list("cause", "Cause", v.causes, true)}
        {list("activity", "Action taken", v.activities, true)}
        {list("mechanism", "Mechanism", v.mechanisms)}
        {list("outcome", "Outcome", v.outcomes)}
      </>}
      {d.disposition === "duplicate" && <Field label="Duplicate of case" width="full">
        <Input width="full" inputMode="numeric" aria-label="Duplicate of case" value={d.duplicate_of ?? ""}
          onChange={set("duplicate_of")} /></Field>}
      <Field label="Note" width="full"><Input width="full" aria-label="Note" value={d.note ?? ""} onChange={set("note")} /></Field>
      <div className="row wrap">
        <Button variant="primary" disabled={!ready || !person || !!a.busy} onClick={save}>
          {missing || data.record ? "Save close-out" : "Close the case"}</Button>
        {onCancel && <Button variant="secondary" onClick={onCancel}>Cancel</Button>}
      </div>
      {a.error && <p role="alert" className="error small">{a.error}</p>}
    </Card>
  );
}

/** The "did the fix hold?" case (P6.1b, #656): whoever closed the problem out answers; a no reopens it as a repeat. */
export function RecheckCard({ firstId, person, closed, readOnly, reload }:
  { firstId: number; person: string; closed: boolean; readOnly: boolean; reload: () => void }) {
  const [note, setNote] = useState("");
  const a = useAct();
  const answer = (held: boolean) => a.run(held ? "held" : "not", () => api.answerRecheck(firstId, person, held, note || undefined), reload);
  return (
    <Card className="recheck" title="Did the fix hold?">
      <p className="small">On <a href={caseHref(firstId)}>case {firstId}</a>. If the problem came back, that case opens again as a repeat.</p>
      {!closed && !readOnly && <>
        <Field label="Note" width="full"><Input width="full" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <div className="row wrap">
          <Button variant="primary" disabled={!person || !!a.busy} onClick={() => answer(true)}>Yes, it held</Button>
          <Button variant="secondary" disabled={!person || !!a.busy} onClick={() => answer(false)}>No, it came back</Button>
        </div>
      </>}
      {a.error && <p role="alert" className="error small">{a.error}</p>}
    </Card>
  );
}
