/** Options with consequences (P5.20, #1251): the realistic choices for a piece of field work side by side, each with
 * when it would be done, the production deferred until then, the risk of waiting and whether it is ready, one of them
 * recommended with its reason. The recommended one is one tap; any choice takes an optional why, kept on the case. */
import { useState, type ReactNode } from "react";
import { api, type CaseOption, type CaseOptions } from "../../api";
import { estimate, money, twoFigures, when } from "../../format";
import { Status, notifyChanged, useAction, useAsync } from "../../lib";
import { ActionStatus, Badge, Button, Card, Field, Input, Line, Notice, Select, Text, type Tone } from "../../ui";
import { person as personName, queue, say } from "../../wording";
import { caseHref } from "./shared";

const RISK: Record<string, Tone> = { Low: "ok", Medium: "warn", High: "danger" };
/** What the button does, said as the act. */
const VERB: Record<CaseOption["key"], string> = {
  dispatch: "Dispatch now", window: "Add to the visit", shelve: "Shelve", escalate: "Escalate",
};
const DOING: Record<CaseOption["key"], string> = {
  dispatch: "Dispatching…", window: "Adding to the visit…", shelve: "Shelving…", escalate: "Escalating…",
};
const DONE: Record<CaseOption["key"], string> = {
  dispatch: "Dispatched", window: "Added to the visit", shelve: "Shelved", escalate: "Escalated",
};
type Choice = Parameters<typeof api.chooseOption>[3];
/** Carries out an option; `name` is how the person it goes to is called, for the confirmation. */
type OnChoose = (c: Choice, name?: string) => void;
const PRIORITY = [["", "as it is"], ["0.9", "High"], ["0.6", "Medium"], ["0.3", "Low"]] as const;

/** The options for the event a case works (read by the case page, which hides its own Send to the field while they
 * show); nothing when there are none to choose (desk work, with the field, the field reported it done). */
export function CaseOptionsCard({ o, person, readOnly = false, onDone }: {
  o: { data?: CaseOptions; reload: () => void }; person: string; readOnly?: boolean; onDone: () => void;
}) {
  return <OptionsLoaded o={o} person={person} readOnly={readOnly} onDone={onDone} />;
}

/** The options for an event on All work, opened from its row. */
export function EventOptions({ exceptionId, person, onDone }: { exceptionId: number; person: string; onDone: () => void }) {
  const o = useAsync(() => api.exceptionOptions(exceptionId), [exceptionId]);
  if (!o.data) return <Status of={o} />;
  if (!o.data.applies) return <Text small>No options to weigh: {o.data.why_not}.</Text>;
  return <OptionsLoaded o={o} person={person} onDone={onDone} />;
}

function OptionsLoaded({ o, person, readOnly = false, onDone }: {
  o: { data?: CaseOptions; reload: () => void }; person: string; readOnly?: boolean; onDone: () => void;
}) {
  const [said, setSaid] = useState<string | null>(null);
  // The card it replaces can be a screen tall, scrolled to its button: the confirmation is brought into view.
  if (said) return <Notice role="status" className="case-options-done"
    ref={(el) => el?.scrollIntoView?.({ block: "nearest" })}>{said}</Notice>;
  const d = o.data;
  if (!d?.applies || d.exception_id == null) return null;
  return <OptionsView d={d} person={person} readOnly={readOnly}
    onChosen={(text) => { setSaid(text); onDone(); notifyChanged(); }} />;
}

function OptionsView({ d, person, readOnly, onChosen }: {
  d: CaseOptions; person: string; readOnly: boolean; onChosen: (said: string) => void;
}) {
  const [open, setOpen] = useState<CaseOption["key"] | null>(null);
  const [acted, setActed] = useState<CaseOption["key"] | null>(null);
  const a = useAction();
  const last = d.decided?.[0];
  const choose = (opt: CaseOption, c: Choice = {}, name?: string) => {
    setActed(opt.key);
    return a.run(DOING[opt.key], () => api.chooseOption(d.exception_id!, person, opt.key, c), () => {
      // who it went to: a dispatch's pick, or the visit's operator a ride along adds a field step for (#1390)
      const who = opt.key === "dispatch" ? c.to : opt.key === "window" ? opt.operator ?? undefined : null;
      const to = who === null ? "" : ` ${opt.key === "window" ? "for" : "to"} ${who && !who.startsWith("role:") ? name ?? personName(who) : `the ${queue(who ?? "operator")}`}`;
      onChosen(`${DONE[opt.key]}${to}${opt.key === d.recommended ? ", as recommended" : ""}. The choice and why are on the case.`);
    });
  };
  return (
    <Card className="case-options" aria-label="Options" title="Options">
      <Cost d={d} />
      {d.field_case && <Text as="div" small>The field already has a case on it:{" "}
        <a href={caseHref(d.field_case.item_id)}>{d.field_case.title}</a></Text>}
      {/* nothing recommended (it can't go now and escalating would come back to the reader): the reason is the line */}
      {!d.recommended && d.reason && <Text as="div" small>{d.reason}</Text>}
      <ul className="case-options-list">
        {d.options.filter((opt) => opt.available).map((opt) => <Option key={opt.key} opt={opt} d={d} readOnly={readOnly || !person} busy={!!a.busy}
          open={open === opt.key} anyOpen={open !== null} onOpen={() => setOpen(open === opt.key ? null : opt.key)}
          onChoose={(c, name) => choose(opt, c, name)}
          status={acted === opt.key ? <ActionStatus busy={a.busy} error={a.error} /> : null} />)}
      </ul>
      {d.options.filter((opt) => !opt.available).map((opt) =>
        <Text as="div" small key={opt.key}><strong>{opt.label}</strong>: {opt.unavailable}</Text>)}
      {last && <Text as="div" small>Last chosen: {d.options.find((x) => x.key === last.chosen)?.label ?? last.chosen},
        {" "}by {personName(last.by)} {when(last.at)}{last.accepted ? ", as recommended" : last.reason ? `: ${last.reason}` : ""}</Text>}
      {d.unknown && d.unknown.length > 0 && <Text as="div" small>Not known: {d.unknown.join("; ")}.</Text>}
    </Card>
  );
}

/** What the event costs while it waits, or what it puts at risk, with what that figure rests on. */
function Cost({ d }: { d: CaseOptions }) {
  if (d.loss) return <div className="case-options-cost">
    <strong>Costing about {estimate(d.loss.oil_bbld)} bbl/d, {money(twoFigures(d.loss.usd_per_day))} a day</strong>
    <Text small>{d.loss.basis}</Text></div>;
  if (d.if_it_trips) return <div className="case-options-cost">
    <strong>Nothing deferred now; about {estimate(d.if_it_trips.oil_bbld)} bbl/d behind it if it trips</strong>
    <Text small>{d.if_it_trips.basis}</Text></div>;
  return <div className="case-options-cost"><strong>Nothing deferred that Fuzer can see</strong></div>;
}

function Option({ opt, d, readOnly, busy, open, anyOpen, onOpen, onChoose, status }: {
  opt: CaseOption; d: CaseOptions; readOnly: boolean; busy: boolean; open: boolean; anyOpen: boolean;
  onOpen: () => void; onChoose: OnChoose;
  /** Dispatching…, or why it failed, under the option that was chosen. */
  status: ReactNode;
}) {
  const rec = opt.key === d.recommended;
  const blocked = opt.ready === false;
  // The recommended option is one tap: it needs nothing more (dispatch goes to the operator queue), unless it can't go.
  const oneTap = rec && !blocked;
  return (
    <li className={`case-option${rec ? " recommended" : ""}`}>
      <Line wrap><strong>{opt.label}</strong>{rec && <Badge tone="ok">Recommended</Badge>}</Line>
      {/* the server words these with whoever is on them as their actor ("user:demo is also on"): named here (#1383) */}
      {opt.detail && <Text as="div" small>{say(opt.detail)}</Text>}
      <div className="case-option-facts">
        <span>Done by {when(opt.by)}</span>
        {/* with nothing deferred now the card's first line says so once, not on every option */}
        {opt.deferred ? <span>about {estimate(opt.deferred.oil_bbl)} bbl, {money(twoFigures(opt.deferred.usd))} deferred</span>
          : d.loss ? <span>nothing deferred</span> : null}
        {opt.risk && <span title={opt.risk.why}>Risk <Badge tone={RISK[opt.risk.level]}>{opt.risk.level.toLowerCase()}</Badge></span>}
        {!blocked && (opt.key === "dispatch" || opt.key === "window") &&
          (opt.unknown?.length ? <Badge tone="muted">unknown</Badge> : <Badge tone="ok">ready</Badge>)}
      </div>
      {blocked && <Text as="div" small><Badge tone="warn">not ready</Badge> {say(opt.blockers?.[0])}</Text>}
      {(opt.unknown ?? []).map((u) => <Text as="div" small key={u}>{say(u)}</Text>)}
      {(opt.clashes ?? []).slice(0, 2).map((c) => <Text as="div" small key={c}><Badge tone="warn">clash</Badge> {say(c)}</Text>)}
      {(opt.warnings ?? []).slice(0, 1).map((w) => <Text as="div" small key={w}><Badge tone="warn">check</Badge> {say(w)}</Text>)}
      {rec && d.reason && <div className="case-option-why">{say(d.reason)}</div>}
      {!readOnly && <div className="case-option-act">
        {oneTap
          ? <Button variant={anyOpen ? "secondary" : "primary"} disabled={busy} onClick={() => onChoose({})}>{VERB[opt.key]}</Button>
          : <Button variant="secondary" disabled={busy} aria-expanded={open} onClick={onOpen}>{VERB[opt.key]}…</Button>}
        {oneTap && opt.key === "dispatch" && <Button variant="ghost" disabled={busy} aria-expanded={open} onClick={onOpen}>Choose who…</Button>}
      </div>}
      {open && !readOnly && <ChoiceForm opt={opt} busy={busy} onChoose={onChoose} />}
      {status}
    </li>
  );
}

/** What carrying out an option needs beyond the tap: who and what for a dispatch, and one optional why, kept on the
 * case. Nothing holds the button back: a dispatch that is not ready names its blocker on the option above. Each field
 * takes the option's width, so none runs out of its card on a phone, a tablet or a desk. */
function ChoiceForm({ opt, busy, onChoose }: {
  opt: CaseOption; busy: boolean; onChoose: OnChoose;
}) {
  const people = useAsync(() => opt.key === "dispatch" ? api.workPeople() : Promise.resolve([]), [opt.key]);
  const [to, setTo] = useState("");
  const [ask, setAsk] = useState("");
  const [due, setDue] = useState("");
  const [priority, setPriority] = useState("");
  const [reason, setReason] = useState("");
  const field = (people.data ?? []).filter((p) => p.roles.includes("operator"));
  return (
    <div className="case-option-form">
      {opt.key === "dispatch" && <>
        <Field label="Send to" width="full"><Select width="full" value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="">{queue("operator")}, whoever is free</option>
          {field.map((p) => <option key={p.name} value={p.name}>{p.display_name || p.name}
            {p.away_until && new Date(p.away_until) > new Date() ? " (away)" : ` · holds ${p.holding ?? 0}`}</option>)}
        </Select></Field>
        <Field label="What to check or do (they see this)" width="full">
          <Input width="full" value={ask} onChange={(e) => setAsk(e.target.value)} /></Field>
        <Field label="Wanted back by" width="full">
          <Input width="full" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
        <Field label="Priority" width="full"><Select width="full" value={priority} onChange={(e) => setPriority(e.target.value)}>
          {PRIORITY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
      </>}
      <Field label="Why (optional, kept on the case)" width="full">
        <Input width="full" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <Button variant="primary" disabled={busy}
        onClick={() => onChoose({
          ...(reason.trim() ? { reason: reason.trim() } : {}), ...(to ? { to } : {}), ...(ask.trim() ? { ask: ask.trim() } : {}),
          ...(due ? { due: new Date(due).toISOString() } : {}), ...(priority ? { priority: Number(priority) } : {}),
        }, field.find((p) => p.name === to)?.display_name || undefined)}>{VERB[opt.key]}</Button>
    </div>
  );
}
