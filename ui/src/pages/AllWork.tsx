/** All work (#502): the IOC's one list of everything that came in. Exceptions, the Work board and the Cases list were
 * three pages showing the same work at different grains; they are views of this page now. Old links land here:
 * `#/board?view=…` opens Ranked by value, `#/cases?state=…` opens In progress or Closed. Case and task pages are
 * unchanged. */
import "../styles/all-work.css";
import { useEffect, useRef, useState } from "react";
import { Input, PageHeader, Select } from "../ui";
import { api } from "../api";
import { href, useAsync, useDebounced } from "../lib";
import { AssignPicker } from "./workflows/shared";
import { ExceptionsPage, backFromField, waitingToDispatch, withTheField, type ExceptionView } from "./Exceptions";
import { BoardPage } from "./Board";
import { CasesPage } from "./workflows/Cases";

export type WorkList = ExceptionView | "progress" | "ranked" | "closed";

export const WORK_LISTS: { key: WorkList; label: string }[] = [
  { key: "dispatch", label: "To dispatch" },
  { key: "field", label: "With the field" },
  { key: "back", label: "Back from the field" },
  { key: "open", label: "All open" },
  { key: "progress", label: "In progress" },
  { key: "ranked", label: "Ranked by value" },
  { key: "closed", label: "Closed" },
];

const KEYS = new Set(WORK_LISTS.map((l) => l.key));

/** A link to one view of All work, with that view's own filters. */
export const workHref = (list: WorkList, query?: Record<string, string | undefined>) =>
  href("/exceptions", { list: list === "dispatch" ? undefined : list, ...query });

/** Where an old Work board or Cases link lands: the same view, filters carried over. */
export function legacyWorkHref(section: "board" | "cases", query: URLSearchParams): string {
  const rest = Object.fromEntries(query.entries());
  if (section === "board") return workHref("ranked", rest);
  const { state, ...others } = rest;
  return workHref(state === "closed" ? "closed" : "progress", others);
}

/** The filters every view of All work shares, kept in the URL so a link or Back keeps them: the kind of work, who
 * holds it (a person, or `role:<name>` for a queue) and words in its title. */
export interface WorkFilters { type?: string; who?: string; q?: string }

const FILTER_KEYS = ["type", "who", "q"] as const;

export function AllWorkPage({ query }: { query: URLSearchParams }) {
  const filters: WorkFilters = Object.fromEntries(FILTER_KEYS.map((k) => [k, query.get(k) || undefined]));
  const asked = query.get("list") as WorkList | null;
  // An old Exceptions link with a state (#/exceptions?state=closed) opens All open with that state.
  const list: WorkList = asked && KEYS.has(asked) ? asked : query.get("state") ? "open" : "dispatch";
  // How many open exceptions each of the IOC's views holds, so what waits shows before a view is opened.
  const open = useAsync(() => api.exceptions({ state: "open" }), []);
  const people = useAsync(() => api.workPeople(), []);
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const rows = open.data ?? [];
  const counts: Partial<Record<WorkList, number>> = open.data ? {
    dispatch: rows.filter(waitingToDispatch).length, field: rows.filter((r) => withTheField(r, operators)).length,
    back: rows.filter(backFromField).length, open: rows.length } : {};
  // On a phone the views scroll sideways; keep the open one in sight.
  const tabs = useRef<HTMLElement>(null);
  useEffect(() => {
    const on = tabs.current?.querySelector<HTMLElement>("a.on");
    if (on && tabs.current) tabs.current.scrollLeft = on.offsetLeft - (tabs.current.clientWidth - on.offsetWidth) / 2;
  }, [list]);
  return (
    <section>
      <PageHeader subtitle="Everything that came in, from every source, one row per event." help="workflow">
        All work</PageHeader>
      <nav ref={tabs} className="tabs work-lists" aria-label="All work views">
        {WORK_LISTS.map((l) => (
          <a key={l.key} href={workHref(l.key, { ...filters })} className={l.key === list ? "on" : ""}
            aria-current={l.key === list ? "page" : undefined}>
            {l.label}{counts[l.key] ? <span className="muted"> {counts[l.key]}</span> : null}</a>
        ))}
      </nav>
      <FilterBar list={list} query={query} filters={filters} />
      {list === "ranked" ? <BoardPage query={query} filters={filters} />
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
    </div>
  );
}
