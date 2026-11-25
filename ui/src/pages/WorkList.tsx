/** All the work, as a List or a Board (the simplification review, #1208): one set of rows from GET /cases, every event
 * and every case, filtered and sorted on any of its dimensions and kept in the URL. To dispatch and Data
 * questions are presets, sets of these filters; a role can save its own as a view. The Board is the same rows laid
 * out in columns by stage, kind, holder, team, source, facility, area or asset. */
import "../styles/all-work.css";
import "../styles/work-board.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type WorkListFilters, type WorkListResult, type WorkListRow, type ExceptionRow } from "../api";
import { Badge, Button, ButtonLink, Card, Checkbox, Empty, ErrorText, Input, Line, PageHeader, Select, Table, Text, Heading } from "../ui";
import { Status, href, useAction, useAsync, useDebounced, usePhone, errorMessage } from "../lib";
import { money, whenDay } from "../format";
import { holder as holderWords, usePerson } from "../work";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { person as personName, queue as queueWords, say } from "../wording";
import { EventRow, DispatchControls, ROW_HEADINGS, STAGE_WORDS, Suppressed, age, type Person, type RowPlace } from "./Exceptions";
import { PriorityBadge, SampleTeam } from "./workflows/shared";
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

export function WorkListPage({ query, board: wantsBoard = false }: { query: URLSearchParams; board?: boolean }) {
  // A phone gets the List as compact cards: a board of columns needs a tablet's or a desk's width.
  const phone = usePhone();
  const board = wantsBoard && !phone;
  const filters: Filters = Object.fromEntries([...KEYS, "group"].map((k) => [k, query.get(k) || undefined]));
  const [person] = usePerson();
  // `me` is whoever is looking, so a preset or a shared link works for everyone.
  const asked: WorkListFilters = { ...Object.fromEntries(KEYS.map((k) => [k, filters[k] === "me" && k === "waiting_on" ? person || undefined : filters[k]])),
    person: person || undefined };
  const list = useAsync(() => api.workList(asked), [JSON.stringify(asked)]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  const mine = (people.data ?? []).find((p) => p.name === person);
  const set = (next: Filters) => location.replace(listHref(board, { ...filters, ...next }));
  return (
    <section>
      <PageHeader className="all-work-header">{board ? "Board" : "All work"}</PageHeader>
      {filters.state !== "closed" && <NewWorkItem onCreated={list.reload} />}
      <Presets board={board} filters={filters} roles={mine?.roles ?? []} />
      <FilterBar board={board} filters={filters} facets={list.data?.facets} labels={list.data?.labels ?? {}} set={set} />
      {!list.data ? <Status of={list} /> : <>
        <Text as="p" small muted>{list.data.total} {filters.state === "closed" ? "closed"
          : filters.state === "all" ? "in every state" : "open"}{list.data.total > list.data.rows.length
          ? `, the first ${list.data.rows.length} shown` : ""}</Text>
        {board ? <Board data={list.data} group={filters.group || "stage"} person={person} people={people.data ?? []}
          queues={queues} onChange={list.reload} />
          : <Rows key={JSON.stringify(filters)} rows={list.data.rows} labels={list.data.labels ?? {}} sort={filters.sort ?? "value"}
            setSort={(sort) => set({ sort })} person={person} people={people.data ?? []} queues={queues} onChange={list.reload} />}
        {!board && filters.state !== "closed" && <Suppressed />}
        <SampleTeam onAdded={people.reload} />
      </>}
    </section>
  );
}

/** All the work about an entity or anything beneath it, as the List's rows (an entity page's Work tab): open by default,
 * closed a tap away, and the List itself, filtered to it, for everything else. */
export function WorkWithin({ within }: { within: string }) {
  const [state, setState] = useState<"open" | "closed">("open");
  const [person] = usePerson();
  const list = useAsync(() => api.workList({ within, state, person: person || undefined }), [within, state, person]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  const [sort, setSort] = useState<string | undefined>();
  const rows = useMemo(() => {
    const out = [...(list.data?.rows ?? [])];
    if (sort === "title") out.sort((a, b) => a.title.localeCompare(b.title));
    if (sort === "due") out.sort((a, b) => (a.due_at ?? "9").localeCompare(b.due_at ?? "9"));
    if (sort === "opened") out.sort((a, b) => a.opened.localeCompare(b.opened));
    return out;
  }, [list.data, sort]);
  return <>
    <Line wrap>
      <div className="chips" role="group" aria-label="Which work">
        {(["open", "closed"] as const).map((s) => <Button key={s} className={state === s ? "chip on" : "chip"} aria-pressed={state === s}
          onClick={() => setState(s)}>{s === "open" ? "Open" : "Closed"}{state === s && list.data ? <Text muted> {list.data.total}</Text> : null}</Button>)}
      </div>
      <a href={listHref(false, { within, ...(state === "closed" ? { state } : {}) })}>Filter and act on these in All work</a>
    </Line>
    {!list.data ? <Status of={list} /> : <Rows key={state} rows={rows} labels={list.data.labels ?? {}} sort={sort ?? "value"}
      setSort={setSort} person={person} people={people.data ?? []} queues={queues} onChange={list.reload} />}
  </>;
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
    if (key.startsWith("page:")) {
      const [path, q] = key.slice(5).split("?");
      return location.assign(href(path, Object.fromEntries(new URLSearchParams(q ?? ""))));
    }
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
        {/* The pages beside the list: what waits on you, and how the work is running per team. */}
        <optgroup label="Other pages">
          <option value="page:/inbox">My work</option>
          {roles.includes("ops_lead") && <option value="page:/handover">Shift handover</option>}
          {roles.includes("ops_lead") && <option value="page:/teams">Team workload</option>}
          <option value="page:/performance">How the work is running</option>
        </optgroup>
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

const MORE_FILTERS = new Set<Key>(["queue", "source", "facility", "area", "asset", "state"]);

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
  // A desk and a tablet show the pickers a lead reaches for every shift; team, source, place and state wait behind More
  // filters, and a chosen one stays out where it can be seen and cleared.
  const more = (key: Key) => MORE_FILTERS.has(key) && !filters[key] ? "work-filter-more" : undefined;
  const pick = (key: Key, label: string, counts: Record<string, number> | undefined, words: (v: string) => string, any: string) => {
    const options = Object.entries(counts ?? {});
    const value = filters[key] ?? "";
    if (value && !options.some(([v]) => v === value)) options.push([value, 0]);
    return <Select aria-label={label} className={more(key)} value={value} onChange={(e) => set({ [key]: e.target.value || undefined })}>
      <option value="">{any}</option>
      {options.map(([v, n]) => <option key={v} value={v}>{v.includes(",") ? `${v.split(",").length} chosen` : words(v)}{n ? ` (${n})` : ""}</option>)}
    </Select>;
  };
  return (
    <div className={open ? "row wrap work-filters open" : "row wrap work-filters"} role="search" aria-label="Filter all work">
      <Input type="search" aria-label="Search titles" placeholder="Search titles" value={text} onChange={(e) => setText(e.target.value)} />
      <Button className="work-filters-toggle" variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="work-filters-phone">Filters{chosen ? ` (${chosen})` : ""}</span>
        <span className="work-filters-desk">{open ? "Fewer filters" : "More filters"}</span></Button>
      {pick("kind", "Kind of work", facets?.kind, (v) => kindLabel[v] ?? v.replace(/_/g, " "), "Every kind")}
      {pick("stage", "Stage", facets?.stage, (v) => STAGE_LABEL[v] ?? v, "Every stage")}
      {pick("holder", "Held by", facets?.holder, holderLabel, "Anyone")}
      {pick("queue", "Team", facets?.queue, teamLabel, "Every team")}
      {pick("source", "Source", facets?.source, (v) => v, "Every source")}
      {pick("facility", "Facility", facets?.facility, named("No facility"), "Every facility")}
      {pick("area", "Area", facets?.area, named("No area"), "Every area")}
      {pick("asset", "Asset", facets?.asset, named("No asset"), "Every asset")}
      <Select aria-label="State" className={more("state")} value={filters.state ?? ""} onChange={(e) => set({ state: e.target.value || undefined })}>
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

/** The List: the rows as a table, one column per thing an IOC desk scans for. A heading with an arrow sorts by it. */
function Rows({ rows, labels, sort, setSort, person, people, queues, onChange }: {
  rows: WorkListRow[]; labels: Record<string, string>; sort: string; setSort: (s: string | undefined) => void;
  person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  // A page of rows at a time, so the List is a screenful or two, not 37,000 px (#8); a new filter starts over.
  const [count, setCount] = useState(PAGE);
  const shown = rows.slice(0, count);
  // A step waiting in a queue the person works is theirs to take, from the row (My work folded into the List).
  const takes = (r: WorkListRow) => (person ? r.take ?? null : null);
  const place = (r: WorkListRow) => ({ stage: r.stage, due: r.due_at, overdue: r.overdue,
    area: r.area !== "none" ? labels[r.area] ?? r.area : r.facility !== "none" ? labels[r.facility] ?? r.facility : "" });
  const head = (h: string) => {
    const by = SORTABLE[h];
    if (!by) return <th key={h}>{h}</th>;
    const on = sort === by;
    return <th key={h} aria-sort={on ? (by === "title" || by === "due" ? "ascending" : "descending") : undefined}>
      <Button variant="link" size="sm" onClick={() => setSort(by === "value" ? undefined : by)}>{h}{on ? " ↓" : ""}</Button></th>;
  };
  return <Card className="work-list-card">
    {!rows.length ? <Empty>Nothing matches these filters.</Empty> :
      <Table className="work-table" aria-label={`${rows.length} shown`}>
        <thead><tr>{ROW_HEADINGS.map(head)}</tr></thead>
        <tbody>
          {shown.map((r) => r.id != null
            ? <EventRow key={r.uid} r={r as unknown as ExceptionRow} place={place(r)} person={person} people={people} queues={queues}
              onChange={onChange} take={takes(r)} />
            : <CaseRow key={r.uid} r={r} place={place(r)} person={person} take={takes(r)} onChange={onChange} />)}
        </tbody>
      </Table>}
    {rows.length > count && <div className="work-more">
      <Button onClick={() => setCount((n) => n + PAGE)}>Show {Math.min(PAGE, rows.length - count)} more</Button>
      <Text small muted>{count} of {rows.length} shown</Text></div>}
  </Card>;
}

/** Rows the List shows before Show more, and cards a Board column shows before Show all. */
const PAGE = 50;
const COLUMN = 20;

/** The headings that sort the list, and what they sort by. */
const SORTABLE: Record<string, string> = { Work: "title", Value: "value", Due: "due", Age: "opened" };

/** A case no event raised (a task, a handover, a close-out): it is worked on its case page. */
function CaseRow({ r, place, person, take, onChange }: {
  r: WorkListRow; place: RowPlace; person: string | null; take: number | null; onChange: () => void;
}) {
  const names = useEntityNames(r.subjects);
  const { busy, error, run } = useAction();
  const open = href(`/cases/${r.item_id}`);
  return <tr className="work-row">
    <td className="work-row-what">
      <div className="work-card-title"><a href={open}>{say(r.title)}</a></div>
      <Text as="div" small muted>{r.kind_label}{r.source ? ` · ${personName(r.source)}` : ""}</Text>
    </td>
    <td className="small work-row-stage">{STAGE_WORDS[r.stage] ?? r.stage}</td>
    <td className="small"><PriorityBadge p={r.priority} type={r.type} /></td>
    <td className="small nowrap">{r.value_usd ? money(r.value_usd) : ""}</td>
    <td className="work-row-where small">{r.subjects.map((s) => <div key={s}><a href={href(`/e/${encodeURIComponent(s)}`)}>
      {display(s, names)}</a></div>)}</td>
    <td className="small">{place.area}</td>
    <td className="work-row-with small"><a href={open}>{holderWords(r.assignee, r.role)}</a></td>
    <td className="small nowrap">{r.due_at ? whenDay(r.due_at) : ""}{r.overdue && <> <Badge tone="danger">overdue</Badge></>}</td>
    <td className="small nowrap">{age(Date.now() - new Date(r.opened).getTime())}</td>
    <td className="work-row-act">{take != null && person && <div className="row work-row-actions">
      <Button size="sm" variant="primary" disabled={!!busy} onClick={() => run("Taking it", () => api.claim(take, person), onChange)}>
        Take it</Button></div>}
      <ErrorText>{error}</ErrorText></td>
  </tr>;
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
  // Below the width every column needs, the board scrolls sideways; a fade on the edge says more columns are there.
  const [more, setMore] = useState({ left: false, right: false });
  // A long column shows its first cards and a Show all, so a board of 300 isn't 28,000 px of cards (#8).
  const [open, setOpen] = useState<Set<string>>(new Set());
  const frame = useRef<HTMLDivElement>(null);
  const edges = () => {
    const el = frame.current;
    if (!el) return;
    const next = { left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 };
    setMore((was) => was.left === next.left && was.right === next.right ? was : next);
  };
  useEffect(() => {
    edges();
    const watch = new ResizeObserver(edges);
    if (frame.current) watch.observe(frame.current);
    return () => watch.disconnect();
  }, [data, group]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <div className={`work-board-frame${more.left ? " more-left" : ""}${more.right ? " more-right" : ""}`}>
    <div className="work-board" ref={frame} onScroll={edges} tabIndex={0} aria-label="Board columns">
      {values.map((v) => {
        const cards = data.rows.filter((r) => key(r) === v);
        const all = open.has(v) || cards.length <= COLUMN;
        return <section key={v} className={`work-board-column${cards.length ? "" : " empty"}`} aria-label={label(v)}>
          <Heading level={2}><span>{label(v)}</span><Text muted>{cards.length}</Text></Heading>
          {(all ? cards : cards.slice(0, COLUMN)).map((r) => <BoardCard key={r.uid} r={r} person={person} people={people} queues={queues} onChange={onChange} />)}
          {!all && <Button size="sm" variant="link" onClick={() => setOpen((o) => new Set(o).add(v))}>Show all {cards.length}</Button>}
        </section>;
      })}
    </div>
  </div>;
}

export function BoardCard({ r, person, people, queues, onChange }: {
  r: WorkListRow; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  const [sending, setSending] = useState(false);
  const said = r.evidence?.field?.[r.evidence.field.length - 1];
  const open = !!person && r.state === "open" && r.id != null;
  // The card's button is the next step: send what nothing routed, close out what came back done, send again what
  // the field could not do. Work with the field is the field's to move.
  const send = open && (r.stage === "dispatch" || (r.stage === "back" && said?.status === "could_not_do"));
  const closeOut = open && r.stage === "back" && said?.status !== "could_not_do" && !!r.item_id;
  const take = person && r.state === "open" ? r.take ?? null : null;
  const taking = useAction();
  return <article className={said?.status === "could_not_do" ? "work-card attention" : "work-card"}>
    <div className="work-card-title" title={r.title}>{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{say(r.title)}</a> : say(r.title)}</div>
    <Text small muted>{r.kind_label}{r.value_usd ? ` · ${money(r.value_usd)}` : ""}</Text>
    <div className="work-card-meta small"><span>{r.item_id && r.stage !== "dispatch" && r.stage !== "planned" ? holderWords(r.assignee, r.role) : "not dispatched"}</span>
      <span>{r.overdue ? <Badge tone="danger">overdue</Badge> : r.due_at ? `due ${whenDay(r.due_at)}` : age(Date.now() - new Date(r.opened).getTime())}</span></div>
    {said && r.stage === "back" && <Text small muted>{said.status === "could_not_do" ? "Could not do" : "Done"}{said.note ? `: ${said.note}` : ""}</Text>}
    {(send || closeOut || take != null) && <div className="row work-card-actions">
      {take != null && <Button size="sm" variant="primary" disabled={!!taking.busy}
        onClick={() => taking.run("Taking it", () => api.claim(take, person!), onChange)}>Take it</Button>}
      {closeOut && <ButtonLink size="sm" variant="primary" href={href(`/cases/${r.item_id}`, { close: "1" })}>Close out</ButtonLink>}
      {send && <Button size="sm" variant="secondary" aria-expanded={sending} onClick={() => setSending(!sending)}>
        {r.stage === "back" ? "Send again" : "Send to"}</Button>}</div>}
    <ErrorText>{taking.error}</ErrorText>
    {send && sending && <DispatchControls r={r as unknown as ExceptionRow} person={person!} people={people} queues={queues}
      onDone={() => { setSending(false); onChange(); }} />}
  </article>;
}
