/** All work (#502): the IOC's one list of everything that came in. Exceptions, the Work board and the Cases list were
 * three pages showing the same work at different grains; they are views of this page now. Old links land here:
 * `#/board?view=…` opens Ranked by value, `#/cases?state=…` opens In progress or Closed. Case and task pages are
 * unchanged. Board lays the open work out by stage, one column per view. */
import "../styles/all-work.css";
import { useEffect, useState } from "react";
import { Button, Input, PageHeader, Select, ErrorText, Line, Text, Tabs } from "../ui";
import { api, type WorkFigures } from "../api";
import { href, useAsync, useDebounced, errorMessage } from "../lib";
import { AssignPicker } from "./workflows/shared";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { usePerson } from "../work";
import { ExceptionsPage, type ExceptionView } from "./Exceptions";
import { BoardPage } from "./Board";
import { CasesPage } from "./workflows/Cases";
import { WorkBoardPage } from "./WorkBoard";
import { WORK_LISTS, workHref } from "../pageTabs";

export type WorkList = ExceptionView | "progress" | "ranked" | "closed" | "board";

export { WORK_LISTS, workHref };

const KEYS = new Set(WORK_LISTS.map((l) => l.key));

/** How many open exceptions each of the IOC's views holds. Home shows the same numbers (#556). */
/** How many open events each of the IOC's views holds, from the one count every page reads (GET /work/counts). */
export const iocCounts = (c: WorkFigures) => ({
  dispatch: c.events.stages.dispatch, field: c.events.stages.field, back: c.events.stages.back, open: c.events.open,
});

/** Where an old Work board or Cases link lands: the same view, filters carried over. */
export function legacyWorkHref(section: "board" | "cases", query: URLSearchParams): string {
  const rest = Object.fromEntries(query.entries());
  if (section === "board") return workHref("ranked", rest);
  const { state, ...others } = rest;
  return workHref(state === "closed" ? "closed" : "progress", others);
}

/** The filters every view of All work shares, kept in the URL so a link or Back keeps them: the kind of work, who
 * holds it (a person, or `role:<name>` for a queue) and words in its title. */
export interface WorkFilters { type?: string; who?: string; q?: string; at?: string }

const FILTER_KEYS = ["type", "who", "q", "at"] as const;

export function AllWorkPage({ query }: { query: URLSearchParams }) {
  const filters: WorkFilters = Object.fromEntries(FILTER_KEYS.map((k) => [k, query.get(k) || undefined]));
  const asked = query.get("list") as WorkList | null;
  // An old Exceptions link with a state (#/exceptions?state=closed) opens All open with that state.
  const list: WorkList = asked && KEYS.has(asked) ? asked : query.get("state") ? "open" : "dispatch";
  const people = useAsync(() => api.workPeople(), []);
  const [person] = usePerson();
  // How many open events each view holds, so what waits shows before a view is opened.
  const tally = useAsync(() => (person ? api.workCounts(person) : Promise.resolve(null)), [person]);
  const counts: Partial<Record<WorkList, number>> = tally.data?.events ? iocCounts(tally.data) : {};
  return (
    <section>
      <PageHeader className="all-work-header"
        actions={<a href={href("/performance")}>How the work is running</a>}>
        All work</PageHeader>
      {/* All the work, as a list or a board: the only switch. Every other view is a filter on it. */}
      <Tabs label="Show all work as" items={[
        { key: "list", label: "List", href: workHref(list === "board" ? "dispatch" : list, { ...filters }), current: list !== "board" },
        { key: "board", label: "Board", href: workHref("board", { ...filters }), current: list === "board" },
      ]} />
      {list !== "board" && <nav className="chips work-views" aria-label="All work views">
        {WORK_LISTS.filter((l) => l.key !== "board").map((l) => (
          <a key={l.key} className={l.key === list ? "chip on" : "chip"} aria-current={l.key === list ? "page" : undefined}
            href={workHref(l.key, { ...filters })}>{l.label}{counts[l.key] ? <Text muted> {counts[l.key]}</Text> : null}</a>
        ))}
        <a className="chip" href={href("/inbox")}>Waiting on me{tally.data?.waiting_on_you?.total
          ? <Text muted> {tally.data.waiting_on_you.total}</Text> : null}</a>
      </nav>}
      <FilterBar list={list} query={query} filters={filters} />
      <SavedViews list={list} filters={filters} roles={(people.data ?? []).find((p) => p.name === person)?.roles ?? []} />
      {list === "ranked" ? <BoardPage query={query} filters={filters} />
        : list === "board" ? <WorkBoardPage query={query} filters={filters} />
        : list === "progress" ? <CasesPage key="progress" query={query} fixedState="open" filters={filters} />
        : list === "closed" ? <CasesPage key="closed" query={query} fixedState="closed" filters={filters} />
        : <ExceptionsPage key={list} query={query} view={list} filters={filters} />}
    </section>
  );
}

/** One filter bar for every view (#502): changing it rewrites the URL in place, so switching views keeps it. */
function FilterBar({ list, query, filters }: { list: WorkList; query: URLSearchParams; filters: WorkFilters }) {
  const kinds = useAsync(() => api.exceptionKinds(), []);
  const [text, setText] = useState(filters.q ?? "");
  // On a phone the two pickers fold behind one Filters button, so the first screen holds work (#1070).
  const [open, setOpen] = useState(false);
  const set_ = [filters.type, filters.who].filter(Boolean).length;
  const words = useDebounced(text);
  const set = (next: WorkFilters) => {
    const keep = Object.fromEntries([...query.entries()].filter(([k]) => k !== "list" && !FILTER_KEYS.includes(k as never)));
    location.replace(workHref(list, { ...keep, ...filters, ...next }));
  };
  useEffect(() => { if ((words || undefined) !== filters.q) set({ q: words || undefined }); }, [words]);  // eslint-disable-line react-hooks/exhaustive-deps
  const types = (kinds.data ?? []).map((k) => [k.name, k.label] as const);
  if (filters.type && !types.some(([n]) => n === filters.type)) types.push([filters.type, filters.type.replace(/_/g, " ")]);
  return (
    <div className={open ? "row wrap work-filters open" : "row wrap work-filters"} role="search" aria-label="Filter all work">
      <Input type="search" aria-label="Search titles" placeholder="Search titles" value={text}
        onChange={(e) => setText(e.target.value)} />
      <Button className="work-filters-toggle" variant="secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
        Filters{set_ ? ` (${set_})` : ""}</Button>
      <Select aria-label="Kind of work" value={filters.type ?? ""} onChange={(e) => set({ type: e.target.value || undefined })}>
        <option value="">All kinds</option>
        {types.map(([n, label]) => <option key={n} value={n}>{label}</option>)}
      </Select>
      <AssignPicker label="Held by" value={filters.who ?? ""} placeholder="Anyone"
        onChange={(v) => set({ who: v || undefined })} />
      {filters.at && <AtPlace id={filters.at} onClear={() => set({ at: undefined })} />}
    </div>
  );
}

/** Home's map sent the person here for one place's work: say which, and let them widen it again. */
function AtPlace({ id, onClear }: { id: string; onClear: () => void }) {
  const ids = id.split(",");
  const names = useEntityNames(id === "unplaced" ? [] : ids);
  return <Line as="span">{id === "unplaced" ? "Not located on the map" : `At ${ids.map((x) => display(x, names)).join(" · ")}`}
    <Button variant="ghost" size="sm" onClick={onClear}>Every place</Button></Line>;
}

/** Saved views per role (P5.1c, #651): pick one saved for your roles, or save the filters you have as a view for a
 * role you hold, so everyone in that role finds it here. */
function SavedViews({ list, filters, roles }: { list: WorkList; filters: WorkFilters; roles: string[] }) {
  const [person] = usePerson();
  const saved = useAsync(() => (person ? api.workViews(person) : Promise.resolve([])), [person]);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!person) return null;
  const views = Array.isArray(saved.data) ? saved.data : [];
  const forRole = role || roles[0] || "";
  const open = (id: string) => {
    const v = views.find((x) => String(x.id) === id);
    if (v) location.assign(workHref(v.list as WorkList, { ...v.filters }));
  };
  const save = async () => {
    setError(null);
    try {
      const kept = Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) as Record<string, string>;
      await api.saveWorkView(person, forRole, name.trim(), list, kept);
      setNaming(false); setName(""); saved.reload();
    } catch (e) { setError(errorMessage(e)); }
  };
  if (!views.length && !roles.length) return null;
  return (
    <div className="row wrap work-views">
      {views.length > 0 && <Select aria-label="Saved views" value="" onChange={(e) => open(e.target.value)}>
        <option value="">Saved views</option>
        {views.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.role.replace(/_/g, " ")})</option>)}
      </Select>}
      {roles.length > 0 && !naming && <Button variant="link" onClick={() => setNaming(true)}>Save this view for a role</Button>}
      {naming && <>
        <Input aria-label="View name" placeholder="Name the view" value={name} onChange={(e) => setName(e.target.value)} />
        {roles.length > 1 && <Select aria-label="For role" value={forRole} onChange={(e) => setRole(e.target.value)}>
          {roles.map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
        </Select>}
        <Button variant="primary" disabled={!name.trim() || !forRole} onClick={save}>
          Save for every {forRole.replace(/_/g, " ")}</Button>
        <Button variant="secondary" onClick={() => { setNaming(false); setError(null); }}>Cancel</Button>
      </>}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
