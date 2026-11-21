/** All the work, as a List or a Board (the simplification review, #1208): one set of rows from GET /cases, every event
 * and every case, filtered and sorted on any of its dimensions and kept in the URL. My work, To dispatch and Data
 * questions are presets, sets of these filters; a role can save its own as a view. The Board is the same rows laid
 * out in columns by stage, kind, holder, team, source, facility, area or asset. */
import "../styles/all-work.css";
import "../styles/work-board.css";
import { useEffect, useState } from "react";
import { api, type WorkListFilters, type WorkListResult, type WorkListRow, type ExceptionRow } from "../api";
import { Badge, Button, ButtonLink, Card, Checkbox, Empty, ErrorText, Input, PageHeader, Select, Text, Heading } from "../ui";
import { Status, href, useAsync, useDebounced, errorMessage } from "../lib";
import { money, whenDay } from "../format";
import { holder as holderWords, usePerson } from "../work";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { queue as queueWords, say } from "../wording";
import { EventRow, DispatchControls, Suppressed, age, type Person } from "./Exceptions";
import { SampleTeam } from "./workflows/shared";
import { NewWorkItem } from "./workflows/Cases";
import { WORK_PRESETS, savedFilters } from "../pageTabs";

const KEYS = ["state", "kind", "stage", "holder", "queue", "waiting_on", "overdue", "subject", "at", "source", "facility",
  "area", "asset", "within", "q", "sort"] as const;
type Key = typeof KEYS[number];
export type Filters = Partial<Record<Key | "group", string>>;

export const STAGE_LABEL: Record<string, string> = {
  dispatch: "To dispatch", desk: "With a desk", planned: "On the plan", field: "With the field",
  back: "Back from the field", closed: "Closed",
};
const STAGE_ORDER = ["dispatch", "desk", "planned", "field", "back", "closed"];
const SORTS: [string, string][] = [["value", "Most valuable"], ["due", "Due soonest"], ["opened", "Newest"],
  ["updated", "Recently changed"], ["title", "Title"]];
const GROUPS: [string, string][] = [["stage", "Stage"], ["kind", "Kind"], ["holder", "Held by"], ["queue", "Team"], ["source", "Source"],
  ["facility", "Facility"], ["area", "Area"], ["asset", "Asset"]];
const PLACE_DIMS = new Set(["facility", "area", "asset"]);

const holderLabel = (h: string) => h === "none" ? "Nobody" : h.startsWith("role:") ? queueWords(h.slice(5)) : holderWords(h, null);
const teamLabel = (q: string) => q === "none" ? "No team" : queueWords(q).replace(/ queue$/, "");

/** The list's link with these filters: unset ones leave it, so the URL says only what is chosen. */
export function listHref(board: boolean, f: Filters) {
  return href(board ? "/board" : "/list", Object.fromEntries(Object.entries(f).filter(([, v]) => v)) as Record<string, string>);
}

export function WorkListPage({ query, board = false }: { query: URLSearchParams; board?: boolean }) {
  const filters: Filters = Object.fromEntries([...KEYS, "group"].map((k) => [k, query.get(k) || undefined]));
  const [person] = usePerson();
  // `me` is whoever is looking, so a preset or a shared link works for everyone.
  const asked: WorkListFilters = Object.fromEntries(KEYS.map((k) => [k, filters[k] === "me" && k === "waiting_on" ? person || undefined : filters[k]]));
  const list = useAsync(() => api.workList(asked), [JSON.stringify(asked)]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  const mine = (people.data ?? []).find((p) => p.name === person);
  const set = (next: Filters) => location.replace(listHref(board, { ...filters, ...next }));
  return (
    <section>
      <PageHeader className="all-work-header">{board ? "Board" : "All work"}</PageHeader>
      <Text as="p" small>{mine?.roles.includes("operator") && <><a href={href("/inbox")}>Your field jobs</a>, with their
        report buttons and kept for no signal. </>}<a href={href("/performance")}>How the work is running</a></Text>
      {filters.state !== "closed" && <NewWorkItem onCreated={list.reload} />}
      <Presets board={board} filters={filters} roles={mine?.roles ?? []} />
      <FilterBar board={board} filters={filters} facets={list.data?.facets} labels={list.data?.labels ?? {}} set={set} />
      {!list.data ? <Status of={list} /> : <>
        <Text as="p" small muted>{list.data.total} {filters.state === "closed" ? "closed"
          : filters.state === "all" ? "in every state" : "open"}{list.data.total > list.data.rows.length
          ? `, the first ${list.data.rows.length} shown` : ""}</Text>
        {board ? <Board data={list.data} group={filters.group || "stage"} person={person} people={people.data ?? []}
          queues={queues} onChange={list.reload} />
          : <Rows rows={list.data.rows} person={person} people={people.data ?? []} queues={queues} onChange={list.reload} />}
        {!board && filters.state !== "closed" && <Suppressed />}
        <SampleTeam onAdded={people.reload} />
      </>}
    </section>
  );
}

/** Presets and saved views: each only sets filters. */
function Presets({ board, filters, roles }: { board: boolean; filters: Filters; roles: string[] }) {
  const [person] = usePerson();
  const saved = useAsync(() => (person ? api.workViews(person) : Promise.resolve([])), [person]);
  const views = Array.isArray(saved.data) ? saved.data : [];
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState<string | null>(null);
  const chosen = Object.fromEntries(Object.entries(filters).filter(([k, v]) => v && k !== "group")) as Record<string, string>;
  const same = (f: Record<string, string>) => JSON.stringify(Object.entries(f).sort()) === JSON.stringify(Object.entries(chosen).sort());
  const current = WORK_PRESETS.find((p) => same(p.filters))?.key ?? views.find((v) => same(savedFilters(v.list, v.filters)))?.id;
  const forRole = role || roles[0] || "";
  const open = (key: string) => {
    if (!key) return location.assign(listHref(board, { group: filters.group }));
    const p = WORK_PRESETS.find((x) => x.key === key);
    const v = views.find((x) => String(x.id) === key);
    const f = p ? p.filters : v ? savedFilters(v.list, v.filters) : undefined;
    if (f) location.assign(listHref(v ? v.list === "board" : board, { ...f, group: filters.group }));
  };
  const save = async () => {
    setError(null);
    try {
      await api.saveWorkView(person!, forRole, name.trim(), board ? "board" : "list", chosen);
      setNaming(false); setName(""); saved.reload();
    } catch (e) { setError(errorMessage(e)); }
  };
  return (
    <div className="row wrap work-presets">
      <Select aria-label="Preset" value={current ? String(current) : ""} onChange={(e) => open(e.target.value)}>
        <option value="">All open work</option>
        {WORK_PRESETS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        {views.length > 0 && <optgroup label="Saved for a role">
          {views.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.role.replace(/_/g, " ")})</option>)}</optgroup>}
      </Select>
      {person && roles.length > 0 && Object.keys(chosen).length > 0 && !current && !naming &&
        <Button variant="link" className="work-views" onClick={() => setNaming(true)}>Save this view for a role</Button>}
      {naming && <>
        <Input aria-label="View name" placeholder="Name the view" value={name} onChange={(e) => setName(e.target.value)} />
        {roles.length > 1 && <Select aria-label="For role" value={forRole} onChange={(e) => setRole(e.target.value)}>
          {roles.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
        </Select>}
        <Button variant="primary" disabled={!name.trim() || !forRole} onClick={save}>Save for every {forRole.replace(/_/g, " ")}</Button>
        <Button variant="secondary" onClick={() => { setNaming(false); setError(null); }}>Cancel</Button>
      </>}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

/** One picker per dimension, each value with how many match it given the other filters. */
function FilterBar({ board, filters, facets, labels, set }: {
  board: boolean; filters: Filters; facets?: WorkListResult["facets"]; labels: Record<string, string>; set: (f: Filters) => void;
}) {
  const named = (none: string) => (v: string) => v === "none" ? none : labels[v] ?? v;
  const kinds = useAsync(() => api.exceptionKinds(), []);
  const kindLabel = Object.fromEntries((kinds.data ?? []).map((k) => [k.name, k.label]));
  const [text, setText] = useState(filters.q ?? "");
  const words = useDebounced(text);
  useEffect(() => { if ((words || undefined) !== filters.q) set({ q: words || undefined }); }, [words]);  // eslint-disable-line react-hooks/exhaustive-deps
  // On a phone the pickers fold behind one Filters button, so the first screen holds work (#1070).
  const [open, setOpen] = useState(false);
  const chosen = KEYS.filter((k) => k !== "q" && k !== "sort" && filters[k]).length;
  const pick = (key: Key, label: string, counts: Record<string, number> | undefined, words: (v: string) => string, any: string) => {
    const options = Object.entries(counts ?? {});
    const value = filters[key] ?? "";
    if (value && !options.some(([v]) => v === value)) options.push([value, 0]);
    return <Select aria-label={label} value={value} onChange={(e) => set({ [key]: e.target.value || undefined })}>
      <option value="">{any}</option>
      {options.map(([v, n]) => <option key={v} value={v}>{v.includes(",") ? `${v.split(",").length} chosen` : words(v)}{n ? ` (${n})` : ""}</option>)}
    </Select>;
  };
  return (
    <div className={open ? "row wrap work-filters open" : "row wrap work-filters"} role="search" aria-label="Filter all work">
      <Input type="search" aria-label="Search titles" placeholder="Search titles" value={text} onChange={(e) => setText(e.target.value)} />
      <Button className="work-filters-toggle" variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
        Filters{chosen ? ` (${chosen})` : ""}</Button>
      {pick("kind", "Kind of work", facets?.kind, (v) => kindLabel[v] ?? v.replace(/_/g, " "), "Every kind")}
      {pick("stage", "Stage", facets?.stage, (v) => STAGE_LABEL[v] ?? v, "Every stage")}
      {pick("holder", "Held by", facets?.holder, holderLabel, "Anyone")}
      {pick("queue", "Team", facets?.queue, teamLabel, "Every team")}
      {pick("source", "Source", facets?.source, (v) => v, "Every source")}
      {pick("facility", "Facility", facets?.facility, named("No facility"), "Every facility")}
      {pick("area", "Area", facets?.area, named("No area"), "Every area")}
      {pick("asset", "Asset", facets?.asset, named("No asset"), "Every asset")}
      <Select aria-label="State" value={filters.state ?? ""} onChange={(e) => set({ state: e.target.value || undefined })}>
        <option value="">Open</option><option value="closed">Closed</option><option value="all">Every state</option>
      </Select>
      <Select aria-label="Sort by" value={filters.sort ?? ""} onChange={(e) => set({ sort: e.target.value || undefined })}>
        {SORTS.map(([v, l]) => <option key={v} value={v === "value" ? "" : v}>{l}</option>)}
      </Select>
      {board && <Select aria-label="Columns by" value={filters.group ?? ""} onChange={(e) => set({ group: e.target.value || undefined })}>
        {GROUPS.map(([v, l]) => <option key={v} value={v === "stage" ? "" : v}>Columns: {l}</option>)}
      </Select>}
      <Checkbox checked={filters.overdue === "true"} onChange={(on) => set({ overdue: on ? "true" : undefined })}>
        Overdue{facets?.overdue ? ` (${facets.overdue})` : ""}</Checkbox>
      {filters.waiting_on && <Chip onClear={() => set({ waiting_on: undefined })}>
        Waiting on {filters.waiting_on === "me" ? "me" : filters.waiting_on}</Chip>}
      {filters.at && <Place id={filters.at} onClear={() => set({ at: undefined })} />}
      {filters.subject && <About id={filters.subject} onClear={() => set({ subject: undefined })} />}
      {filters.within && <Within id={filters.within} onClear={() => set({ within: undefined })} />}
      {(chosen > 0 || filters.q) && <Button variant="link" onClick={() => { setText(""); location.replace(listHref(board, { group: filters.group })); }}>
        Clear filters</Button>}
    </div>
  );
}

function Chip({ children, onClear }: { children: React.ReactNode; onClear: () => void }) {
  return <span className="row work-filter-chip">{children}<Button variant="ghost" size="sm" aria-label="Remove this filter"
    onClick={onClear}>×</Button></span>;
}

function Place({ id, onClear }: { id: string; onClear: () => void }) {
  const ids = id.split(",");
  const names = useEntityNames(id === "unplaced" ? [] : ids);
  return <Chip onClear={onClear}>{id === "unplaced" ? "Not located on the map" : `At ${ids.map((x) => display(x, names)).join(" · ")}`}</Chip>;
}

function About({ id, onClear }: { id: string; onClear: () => void }) {
  const names = useEntityNames([id]);
  return <Chip onClear={onClear}>About {display(id, names)}</Chip>;
}

function Within({ id, onClear }: { id: string; onClear: () => void }) {
  const ids = id.split(",");
  const names = useEntityNames(ids);
  return <Chip onClear={onClear}>In {ids.map((x) => display(x, names)).join(" · ")}</Chip>;
}

function Rows({ rows, person, people, queues, onChange }: {
  rows: WorkListRow[]; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  return <Card className="work-list-card">
    {!rows.length ? <Empty>Nothing matches these filters.</Empty> : <>
      <div className="work-list-head small muted" aria-hidden="true">
        <span>Work</span><span>Entities</span><span>Age</span><span>With</span><span /></div>
      <ul className="work-list" aria-label={`${rows.length} shown`}>
        {rows.map((r) => r.id != null
          ? <EventRow key={r.uid} r={r as unknown as ExceptionRow} person={person} people={people} queues={queues} onChange={onChange} />
          : <CaseRow key={r.uid} r={r} />)}
      </ul>
    </>}
  </Card>;
}

/** A case no event raised (a task, a handover, a close-out): it is worked on its case page. */
function CaseRow({ r }: { r: WorkListRow }) {
  const names = useEntityNames(r.subjects);
  const open = href(`/cases/${r.item_id}`);
  return <li className="work-card work-row">
    <div className="work-row-what">
      <div className="work-card-title"><a href={open}>{say(r.title)}</a></div>
      <Text as="div" small muted>{r.kind_label}{r.source ? ` · ${r.source}` : ""}</Text>
    </div>
    <div className="work-row-where small">{r.subjects.map((s) => <div key={s}><a href={href(`/e/${encodeURIComponent(s)}`)}>
      {display(s, names)}</a></div>)}</div>
    <div className="work-row-age small"><strong>{age(Date.now() - new Date(r.opened).getTime())}</strong>
      {r.due_at && <Text muted> · due {whenDay(r.due_at)}</Text>}{r.overdue && <> <Badge tone="danger">overdue</Badge></>}</div>
    <div className="work-row-with small"><a href={open}>{holderWords(r.assignee, r.role)}</a></div>
    <div />
  </li>;
}

/** The same rows in columns: by stage (in the order work moves), or by kind, holder, team, source, facility, area or asset. */
function Board({ data, group, person, people, queues, onChange }: {
  data: WorkListResult; group: string; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  const key = (r: WorkListRow) => String(r[group as keyof WorkListRow] ?? "none");
  const label = (v: string) => group === "stage" ? STAGE_LABEL[v] ?? v : group === "holder" ? holderLabel(v)
    : group === "queue" ? teamLabel(v) : group === "kind" ? data.rows.find((r) => r.kind === v)?.kind_label ?? v
    : PLACE_DIMS.has(group) ? (v === "none" ? `No ${group}` : data.labels?.[v] ?? v) : v;
  const values = group === "stage"
    ? STAGE_ORDER.filter((s) => s !== "closed" || data.rows.some((r) => r.stage === "closed"))
    : [...new Set(data.rows.map(key))];
  const [shown, setShown] = useState<string | null>(null);
  const showing = shown ?? values.find((v) => data.rows.some((r) => key(r) === v)) ?? values[0];
  return <>
    <nav className="chips work-board-stages" aria-label="Columns">
      {values.map((v) => <Button key={v} className="chip" aria-pressed={v === showing} onClick={() => setShown(v)}>
        {label(v)} <Text muted>{data.rows.filter((r) => key(r) === v).length}</Text></Button>)}
    </nav>
    <div className="work-board">
      {values.map((v) => {
        const cards = data.rows.filter((r) => key(r) === v);
        return <section key={v} className={`work-board-column${cards.length ? "" : " empty"}${v === showing ? " shown" : ""}`}
          aria-label={label(v)}>
          <Heading level={2}><span>{label(v)}</span><Text muted>{cards.length}</Text></Heading>
          {cards.map((r) => <BoardCard key={r.uid} r={r} person={person} people={people} queues={queues} onChange={onChange} />)}
        </section>;
      })}
    </div>
  </>;
}

function BoardCard({ r, person, people, queues, onChange }: {
  r: WorkListRow; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  const [sending, setSending] = useState(false);
  const said = r.evidence?.field?.[r.evidence.field.length - 1];
  const open = !!person && r.state === "open" && r.id != null;
  // The card's button is the next step: send what nothing routed, close out what came back done, send again what
  // the field could not do. Work with the field is the field's to move.
  const send = open && (r.stage === "dispatch" || (r.stage === "back" && said?.status === "could_not_do"));
  const closeOut = open && r.stage === "back" && said?.status !== "could_not_do" && !!r.item_id;
  return <article className={said?.status === "could_not_do" ? "work-card attention" : "work-card"}>
    <div className="work-card-title" title={r.title}>{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{say(r.title)}</a> : say(r.title)}</div>
    <Text small muted>{r.kind_label}{r.value_usd ? ` · ${money(r.value_usd)}` : ""}</Text>
    <div className="work-card-meta small"><span>{r.item_id ? holderWords(r.assignee, r.role) : "not dispatched"}</span>
      <span>{r.overdue ? <Badge tone="danger">overdue</Badge> : r.due_at ? `due ${whenDay(r.due_at)}` : age(Date.now() - new Date(r.opened).getTime())}</span></div>
    {said && r.stage === "back" && <Text small muted>{said.status === "could_not_do" ? "Could not do" : "Done"}{said.note ? `: ${said.note}` : ""}</Text>}
    {(send || closeOut) && <div className="row work-card-actions">
      {closeOut && <ButtonLink size="sm" variant="primary" href={href(`/cases/${r.item_id}`, { close: "1" })}>Close out</ButtonLink>}
      {send && <Button size="sm" variant="secondary" aria-expanded={sending} onClick={() => setSending(!sending)}>
        {r.stage === "back" ? "Send again" : "Send to"}</Button>}</div>}
    {send && sending && <DispatchControls r={r as unknown as ExceptionRow} person={person!} people={people} queues={queues}
      onDone={() => { setSending(false); onChange(); }} />}
  </article>;
}
