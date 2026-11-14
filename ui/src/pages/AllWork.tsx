/** All work (#502): the IOC's one list of everything that came in. Exceptions, the Work board and the Cases list were
 * three pages showing the same work at different grains; they are views of this page now. Old links land here:
 * `#/board?view=…` opens Ranked by value, `#/cases?state=…` opens In progress or Closed. Case and task pages are
 * unchanged. Board lays the open work out by stage, one column per view. */
import "../styles/all-work.css";
import { useEffect, useRef, useState } from "react";
import { Button, Input, PageHeader, Select } from "../ui";
import { api, type ExceptionRow } from "../api";
import { href, useAsync, useDebounced } from "../lib";
import { AssignPicker } from "./workflows/shared";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { usePerson } from "../work";
import { ExceptionsPage, backFromField, waitingToDispatch, withTheField, type ExceptionView } from "./Exceptions";
import { BoardPage } from "./Board";
import { CasesPage } from "./workflows/Cases";
import { WorkBoardPage } from "./WorkBoard";

export type WorkList = ExceptionView | "progress" | "ranked" | "closed" | "board";

export const WORK_LISTS: { key: WorkList; label: string }[] = [
  { key: "dispatch", label: "To dispatch" },
  { key: "field", label: "With the field" },
  { key: "back", label: "Back from the field" },
  { key: "open", label: "All open" },
  { key: "progress", label: "In progress" },
  { key: "ranked", label: "Ranked by value" },
  { key: "closed", label: "Closed" },
  { key: "board", label: "Board" },
];

const KEYS = new Set(WORK_LISTS.map((l) => l.key));

/** A link to one view of All work, with that view's own filters. */
export const workHref = (list: WorkList, query?: Record<string, string | undefined>) =>
  href("/exceptions", { list: list === "dispatch" ? undefined : list, ...query });

/** How many open exceptions each of the IOC's views holds. Home shows the same numbers (#556). */
export const iocCounts = (rows: ExceptionRow[], operators: Set<string>) => ({
  dispatch: rows.filter(waitingToDispatch).length, field: rows.filter((r) => withTheField(r, operators)).length,
  back: rows.filter(backFromField).length, open: rows.length,
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
  // How many open exceptions each of the IOC's views holds, so what waits shows before a view is opened.
  const open = useAsync(() => api.exceptions({ state: "open" }), []);
  const people = useAsync(() => api.workPeople(), []);
  const [person] = usePerson();
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const rows = open.data ?? [];
  const counts: Partial<Record<WorkList, number>> = open.data ? iocCounts(rows, operators) : {};
  // On a phone the views scroll sideways; keep the open one in sight.
  const tabs = useRef<HTMLElement>(null);
  useEffect(() => {
    const on = tabs.current?.querySelector<HTMLElement>("a.on");
    if (on && tabs.current) tabs.current.scrollLeft = on.offsetLeft - (tabs.current.clientWidth - on.offsetWidth) / 2;
  }, [list]);
  return (
    <section>
      <PageHeader subtitle="Everything that came in, from every source, one row per event." help="workflow"
        actions={<a href={href("/performance")}>How the work is running</a>}>
        All work</PageHeader>
      <nav ref={tabs} className="tabs work-lists" aria-label="All work views">
        {WORK_LISTS.map((l) => (
          <a key={l.key} href={workHref(l.key, { ...filters })} className={l.key === list ? "on" : ""}
            aria-current={l.key === list ? "page" : undefined}>
            {l.label}{counts[l.key] ? <span className="muted"> {counts[l.key]}</span> : null}</a>
        ))}
      </nav>
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
  const words = useDebounced(text);
  const set = (next: WorkFilters) => {
    const keep = Object.fromEntries([...query.entries()].filter(([k]) => k !== "list" && !FILTER_KEYS.includes(k as never)));
    location.replace(workHref(list, { ...keep, ...filters, ...next }));
  };
  useEffect(() => { if ((words || undefined) !== filters.q) set({ q: words || undefined }); }, [words]);  // eslint-disable-line react-hooks/exhaustive-deps
  const types = (kinds.data ?? []).map((k) => [k.name, k.label] as const);
  if (filters.type && !types.some(([n]) => n === filters.type)) types.push([filters.type, filters.type.replace(/_/g, " ")]);
  return (
    <div className="row wrap work-filters" role="search" aria-label="Filter all work">
      <Select aria-label="Kind of work" value={filters.type ?? ""} onChange={(e) => set({ type: e.target.value || undefined })}>
        <option value="">All kinds</option>
        {types.map(([n, label]) => <option key={n} value={n}>{label}</option>)}
      </Select>
      <AssignPicker label="Held by" value={filters.who ?? ""} placeholder="Anyone"
        onChange={(v) => set({ who: v || undefined })} />
      <Input type="search" aria-label="Search titles" placeholder="Search titles" value={text}
        onChange={(e) => setText(e.target.value)} />
      {filters.at && <AtPlace id={filters.at} onClear={() => set({ at: undefined })} />}
    </div>
  );
}

/** Home's map sent the person here for one place's work: say which, and let them widen it again. */
function AtPlace({ id, onClear }: { id: string; onClear: () => void }) {
  const ids = id.split(",");
  const names = useEntityNames(ids);
  return <span className="row">At {ids.map((x) => display(x, names)).join(" · ")}
    <Button variant="ghost" size="sm" onClick={onClear}>Every place</Button></span>;
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
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
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
      {error && <p role="alert" className="error small">{error}</p>}
    </div>
  );
}
