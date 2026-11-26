/** Structured close-out (P6.1a, #655): closing a case says what it was and, for a real problem, what failed, why and
 * what was done, from the ISO 14224 lists. The form comes pre-filled from the case's own words or an agent's
 * suggestion, and the person confirms it. On a phone it is a handful of selects, one per line. */
import { useState } from "react";
import { when } from "../format";
import { api, type CaseCloseout, type CloseoutVocabulary } from "../api";
import { Button, Card, Disclosure, Field, Input, Select, ErrorText, Line, Text, Notice } from "../ui";
import { Status, useAsync, useAction } from "../lib";
import { Actor } from "../work";
import { AssignPicker, caseHref } from "./workflows/shared";
import { say } from "../wording";

type Draft = Partial<Record<"disposition" | "failure_mode" | "mechanism" | "cause" | "activity" | "outcome" | "note", string>>
  & { duplicate_of?: string };

const pick = (list: Record<string, string>, code?: string | null) => (code ? list[code] ?? code : "");

/** One line for a recorded close-out, as the case's history reads it. */
export function closeoutLine(r: CaseCloseout, v: CloseoutVocabulary): string {
  const what = v.dispositions[r.disposition] ?? r.disposition;
  if (r.disposition === "duplicate") return `${what} of case ${r.duplicate_of}`;
  if (r.disposition !== "real") return what;
  const terms = [r.failure_mode && pick(v.failure_modes, r.failure_mode), r.cause && `cause ${pick(v.causes, r.cause).toLowerCase()}`,
    r.activity && pick(v.activities, r.activity).toLowerCase()].filter(Boolean).join(", ");
  const outcome = r.outcome ? pick(v.outcomes, r.outcome).toLowerCase() : "";
  return [terms ? `${what}: ${terms}` : what, outcome].filter(Boolean).join("; ");
}

export function CaseCloseoutCard({ itemId, person, closed, readOnly, reload, startOpen = false, openItems = 0 }:
  { itemId: number; person: string; closed: boolean; readOnly: boolean; reload: () => void; startOpen?: boolean;
    /** checklist items still open: finishing leaves them open in the record */ openItems?: number }) {
  const form = useAsync(() => api.caseCloseout(itemId), [itemId]);
  const [open, setOpen] = useState(startOpen && !closed);
  if (!form.data) return form.error ? <Status error={form.error} onRetry={form.reload} /> : null;
  const { record, vocabulary: v } = form.data;
  if (record && !open) {
    return (
      <Card className="closeout-done" title="Close-out">
        <Text as="p" small>{closeoutLine(record, v)}{record.note && <Text muted> · {say(record.note)}</Text>}</Text>
        <Text as="p" small muted>by <Actor who={record.by} /> {when(record.at)}
          {!readOnly && <> · <Button variant="link" onClick={() => setOpen(true)}>Correct it</Button></>}</Text>
        {form.data.follow_up != null && <Text as="p" small>Follow-up: <a href={caseHref(form.data.follow_up)}>case {form.data.follow_up}</a></Text>}
        {record.held != null
          ? <Text as="p" small>{record.held ? "The fix held" : "The fix did not hold"}, said <Actor who={record.held_by ?? ""} />
            {record.held_note && <Text muted>: {record.held_note}</Text>}</Text>
          : record.recheck_at && <Text as="p" small muted>Re-check {when(record.recheck_at)}: did the fix hold?</Text>}
        {(form.data.repeats ?? []).length > 0 && <Notice>Came back:{" "}
          {(form.data.repeats ?? []).map((r, i) => <span key={r.again}>{i > 0 && ", "}<a href={caseHref(r.again)}>case {r.again}</a></span>)}</Notice>}
      </Card>
    );
  }
  if (readOnly) return closed ? <Text as="p" small muted>Close-out missing.</Text> : null;
  // work someone asked for is finished, not closed out as a problem (the glossary's Finish)
  if (!closed && !open) return <Button variant="primary" onClick={() => setOpen(true)}>{form.data.asked ? "Finish" : "Close out"}</Button>;
  return <CloseoutForm itemId={itemId} person={person} data={form.data} missing={closed && !record} openItems={openItems}
    onDone={() => { setOpen(false); form.reload(); reload(); }} onCancel={closed && !record ? undefined : () => setOpen(false)} />;
}

function CloseoutForm({ itemId, person, data, missing, onDone, onCancel, openItems = 0 }: {
  itemId: number; person: string; data: NonNullable<Awaited<ReturnType<typeof api.caseCloseout>>>; missing: boolean;
  onDone: () => void; onCancel?: () => void; openItems?: number;
}) {
  const v = data.vocabulary;
  // asked work is done as asked or a duplicate; "done as asked" never fits a problem an exception raised; log only is
  // for a message that only said what was done
  const dispositions = Object.fromEntries(Object.entries(v.dispositions)
    .filter(([code]) => (data.asked ? ["done", "duplicate"].includes(code) : code !== "done" && (code !== "log_only" || data.message))));
  const s = data.suggestion;
  // a suggested disposition the form doesn't offer here fills nothing: the form shows Choose, and claims nothing
  const offered = s.disposition && dispositions[s.disposition] ? s.disposition : undefined;
  const start: Draft = data.record
    ? Object.fromEntries(Object.entries(data.record).filter(([, x]) => x != null).map(([k, x]) => [k, String(x)]))
    : offered ? { disposition: offered, outcome: s.outcome, ...(data.equipment !== false
        // off equipment the failure terms sit in a folded, optional section: left for the person to open and set
        ? { failure_mode: s.failure_mode, mechanism: s.mechanism, cause: s.cause, activity: s.activity } : {}) } : {};
  const [d, setD] = useState<Draft>(start);
  const [followTo, setFollowTo] = useState("");
  const a = useAction();
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD({ ...d, [k]: e.target.value || undefined });
  const real = d.disposition === "real";
  // equipment-failure terms are required only on an equipment case (#1145); elsewhere they are optional
  const needsTerms = real && data.equipment !== false;
  const missingTerms = needsTerms ? ([["failure mode", d.failure_mode], ["cause", d.cause], ["action taken", d.activity]] as const)
    .filter(([, x]) => !x).map(([n]) => n) : [];
  const ready = !!d.disposition && missingTerms.length === 0
    && (d.disposition !== "duplicate" || !!Number(d.duplicate_of));
  const hint = !d.disposition ? "Say what it was first"
    : missingTerms.length ? `Still needed: ${missingTerms.join(", ")}`
    : d.disposition === "duplicate" && !Number(d.duplicate_of) ? "Say which case it duplicates" : undefined;
  // "Filled in" sits beside each field the pre-fill filled and still shows, never as one line over the form: a folded
  // section is never filled (the server leaves it out), so nothing is saved that the person didn't see (#1366)
  const suggested = !data.record && !!offered && !data.asked;
  const from = s.from === "agent" ? "the agent's suggestion" : "what the case says";
  const filled = (k: keyof Draft) => {
    const was = (s as Partial<Record<keyof Draft, string>>)[k];
    return suggested && was != null && d[k] === was;
  };
  const list = (k: keyof Draft, label: string, options: Record<string, string>, required = false) => (
    <Field label={label} width="full" hint={filled(k) ? `Filled in from ${from}` : undefined}>
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
    follow_up_to: d.outcome === "contained" && followTo && data.follow_up == null ? followTo : undefined,
  }), onDone);
  // Contained means a follow-up is planned: it is sent from here, as a case of its own, unless one already went
  const followUp = d.outcome === "contained" && data.follow_up == null && <Field label="Send the follow-up to" width="full">
    <AssignPicker value={followTo} onChange={setFollowTo} label="Send the follow-up to" /></Field>;
  return (
    <Card className="closeout-form" title={missing ? "Close-out missing" : data.asked ? "Finish" : "Close out"}>
      {openItems > 0 && !missing && <Notice tone="warn">{openItems === 1 ? "1 checklist item is" : `${openItems} checklist items are`} still
        open. Tick each one done or mark it not needed first, or finish and they stay open in the record.</Notice>}
      {missing && <Text as="p" small>This case closed without saying what it was. Fill it in so it counts.</Text>}
      {list("disposition", "What it was", dispositions, true)}
      {needsTerms && <>
        {list("failure_mode", "Failure mode", v.failure_modes, true)}
        {list("cause", "Cause", v.causes, true)}
        {list("activity", "Action taken", v.activities, true)}
        {list("mechanism", "Mechanism", v.mechanisms)}
        {list("outcome", "Outcome", v.outcomes)}
        {followUp}
      </>}
      {real && !needsTerms && <>
        {list("outcome", "Outcome", v.outcomes)}
        {followUp}
        <Disclosure className="small" label={<>Equipment failure details (optional)</>}>
          {list("failure_mode", "Failure mode", v.failure_modes)}
          {list("cause", "Cause", v.causes)}
          {list("activity", "Action taken", v.activities)}
          {list("mechanism", "Mechanism", v.mechanisms)}
        </Disclosure>
      </>}
      {d.disposition === "duplicate" && <Field label="Duplicate of case" width="full">
        <Input width="full" inputMode="numeric" aria-label="Duplicate of case" value={d.duplicate_of ?? ""}
          onChange={set("duplicate_of")} /></Field>}
      <Field label="Note" width="full"><Input width="full" aria-label="Note" value={d.note ?? ""} onChange={set("note")} /></Field>
      <Line wrap>
        <Button variant="primary" disabled={!ready || !person || !!a.busy} onClick={save}>
          {missing || data.record ? "Save close-out" : data.asked ? "Finish the case" : "Close the case"}</Button>
        {onCancel && <Button variant="secondary" onClick={onCancel}>Cancel</Button>}
      </Line>
      {hint && !a.busy && <Text as="p" small muted>{hint}.</Text>}
      <ErrorText>{a.error}</ErrorText>
    </Card>
  );
}

/** The "did the fix hold?" case (P6.1b, #656): whoever closed the problem out answers; a no reopens it as a repeat. */
export function RecheckCard({ firstId, person, closed, readOnly, reload }:
  { firstId: number; person: string; closed: boolean; readOnly: boolean; reload: () => void }) {
  const [note, setNote] = useState("");
  const a = useAction();
  const answer = (held: boolean) => a.run(held ? "held" : "not", () => api.answerRecheck(firstId, person, held, note || undefined), reload);
  return (
    <Card className="recheck" title="Did the fix hold?">
      <Text as="p" small>On <a href={caseHref(firstId)}>case {firstId}</a>. If the problem came back, that case opens again as a repeat.</Text>
      {!closed && !readOnly && <>
        <Field label="Note" width="full"><Input width="full" aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <Line wrap>
          <Button variant="primary" disabled={!person || !!a.busy} onClick={() => answer(true)}>Yes, it held</Button>
          <Button variant="secondary" disabled={!person || !!a.busy} onClick={() => answer(false)}>No, it came back</Button>
        </Line>
      </>}
      <ErrorText>{a.error}</ErrorText>
    </Card>
  );
}
