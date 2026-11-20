// App search: one box for the whole app, opened from the magnifying glass at the top left (the phone's top bar, the
// sidebar on a wider screen) or with / or Ctrl+K. It finds pages, the tabs inside pages and records in the register,
// and sends a question to Ask the same way Ask's own box does.
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api } from "./api";
import { usePermission } from "./Auth";
import { KindBadge, entityHref, href, useAsync, useDebounced } from "./lib";
import { NAV, isActive, useNavigationAccess, type NavGroup, type NavItem } from "./nav";
import { MASTER_DATA_TABS, SETTINGS_VIEWS, SOURCES_VIEWS, WORK_PRESETS } from "./pageTabs";
import { Button, ErrorText, Input, Heading } from "./ui";

const OPEN = "fuzer:search";
const ENTITY_LIMIT = 8;

/** Open app search from anywhere. */
export const openSearch = () => window.dispatchEvent(new Event(OPEN));

const GLASS = "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16.5 16.5L21 21";

/** The magnifying glass. `wide` draws it as a search field (the sidebar); otherwise it is an icon (the top bar). */
export function SearchButton({ wide = false }: { wide?: boolean }) {
  return <Button variant="ghost" className={wide ? "search-button wide" : "search-button"} aria-label="Search" title="Search (/)"
    aria-haspopup="dialog" onClick={openSearch}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d={GLASS} /></svg>
    {wide && <span aria-hidden="true">Search</span>}
  </Button>;
}

interface Result { id: string; group: "Ask" | "Pages" | "Tabs" | "Entities" | "More"; label: string; detail?: string; kind?: string; to: string }

/** A question rather than a name: ends in a question mark or starts like one. It puts Ask first. */
const QUESTION = /\?\s*$|^(what|why|how|which|when|where|who|whose|is|are|was|were|does|do|did|can|could|should|will|show|list|compare|explain|summari[sz]e|tell)\b/i;

const words = (text: string) => text.toLowerCase().split(/[^\p{L}\p{N}-]+/u).filter(Boolean);
/** Every typed word starts a word of the label: "dis" finds To dispatch, "re" does not find Explore. A page's group counts
 * once three letters are typed, so "rep" lists the Reports pages. */
const matches = (q: string[], label: string, group?: string) => {
  const own = words(label), theirs = group ? words(group) : [];
  return q.every((w) => own.some((x) => x.startsWith(w)) || (w.length >= 3 && theirs.some((x) => x.startsWith(w))));
};
const pageHref = (item: NavItem) => item.external ?? href(item.path ?? `/${item.section}`);

/** Each page's tabs, by the section of the page that draws them. */
const TABS: { section: string; tabs: { label: string; to: string }[] }[] = [
  { section: "list", tabs: WORK_PRESETS.map((t) => ({ label: t.label, to: href("/list", t.filters) })) },
  { section: "masterdata", tabs: MASTER_DATA_TABS.map((t) => ({ label: t.label, to: href("/masterdata", { tab: t.key }) })) },
  { section: "sources", tabs: SOURCES_VIEWS.map((t) => ({ label: t.label, to: href("/sources", { view: t.key }) })) },
  { section: "settings", tabs: SETTINGS_VIEWS.map((t) => ({ label: t.label, to: href("/settings", { view: t.key || undefined }) })) },
];

function usePlaces() {
  const allowed = useNavigationAccess();
  return useMemo(() => {
    const pages: { group: NavGroup; item: NavItem }[] = [];
    for (const group of NAV) if (allowed(group)) for (const item of group.items) if (allowed(item)) pages.push({ group, item });
    const tabs = TABS.flatMap(({ section, tabs }) => {
      const page = pages.find(({ item }) => isActive(item, section) && !item.view);
      return page ? tabs.map((t) => ({ ...t, page: page.item.label })) : [];
    });
    return { pages, tabs };
  }, [allowed]);
}

export function AppSearch() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const dialog = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const ai = usePermission("ai");
  const { pages, tabs } = usePlaces();
  const q = text.trim();
  const debounced = useDebounced(q, 200);
  const hits = useAsync(() => (open && debounced ? api.search(debounced, undefined, ENTITY_LIMIT + 1) : Promise.resolve([])), [open, debounced]);

  useEffect(() => {
    const show = () => setOpen(true);
    const key = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, select, [contenteditable='true']");
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    const close = () => setOpen(false);
    window.addEventListener(OPEN, show);
    window.addEventListener("keydown", key);
    window.addEventListener("hashchange", close);
    return () => { window.removeEventListener(OPEN, show); window.removeEventListener("keydown", key); window.removeEventListener("hashchange", close); };
  }, []);

  // While open: the page behind does not scroll, Escape closes, Tab stays inside, and focus returns where it was.
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    input.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
      if (e.key !== "Tab" || !dialog.current) return;
      const items = Array.from(dialog.current.querySelectorAll<HTMLElement>("input, button, a[href]"));
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", key);
    return () => { document.body.style.overflow = overflow; window.removeEventListener("keydown", key); before?.focus?.(); };
  }, [open]);

  const results = useMemo((): Result[] => {
    const w = words(q);
    const found: Result[] = [];
    if (!w.length) return pages.filter(({ item }) => !item.external).map(({ group, item }) => ({
      id: `p:${group.label}:${item.label}`, group: "Pages", label: item.label, detail: item.label === group.label ? undefined : group.label, to: pageHref(item) }));
    const ask: Result[] = ai ? [{ id: "ask", group: "Ask", label: q, to: href("/ask", { q, send: "1" }) }] : [];
    if (QUESTION.test(q)) found.push(...ask);
    for (const { group, item } of pages) if (matches(w, item.label, group.label))
      found.push({ id: `p:${group.label}:${item.label}`, group: "Pages", label: item.label, detail: item.label === group.label ? undefined : group.label, to: pageHref(item) });
    for (const t of tabs) if (matches(w, t.label, t.page))
      found.push({ id: `t:${t.to}`, group: "Tabs", label: t.label, detail: t.page, to: t.to });
    for (const h of (debounced === q ? hits.data ?? [] : []).slice(0, ENTITY_LIMIT))
      found.push({ id: `e:${h.fuzer_id}`, group: "Entities", label: h.label ?? h.fuzer_id, detail: h.description ?? undefined, kind: h.kind, to: entityHref(h.fuzer_id) });
    if (debounced === q && (hits.data?.length ?? 0) > ENTITY_LIMIT)
      found.push({ id: "browse", group: "More", label: `All entities matching “${q}”`, to: href("/browse", { q }) });
    if (!QUESTION.test(q)) found.push(...ask);
    return found;
  }, [q, debounced, pages, tabs, hits.data, ai]);

  useEffect(() => setActive(0), [q]);
  const current = Math.min(active, Math.max(results.length - 1, 0));

  const go = (to: string) => {
    setOpen(false);
    setText("");
    if (to.startsWith("#")) window.location.hash = to.slice(1);
    else window.open(to, "_blank", "noreferrer");
  };
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((current + 1) % Math.max(results.length, 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((current - 1 + results.length) % Math.max(results.length, 1)); }
    else if (e.key === "Enter" && results[current]) { e.preventDefault(); go(results[current].to); }
  };
  useEffect(() => {
    dialog.current?.querySelector(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const searching = !!q && (debounced !== q || hits.loading);
  const groups = [...new Set(results.map((r) => r.group))];
  if (!open) return null;
  return (<>
    <div className="app-search-scrim" onClick={() => setOpen(false)} />
    <div ref={dialog} className="app-search" role="dialog" aria-modal="true" aria-label="Search">
        <div className="app-search-bar">
          <Input ref={input} type="search" role="combobox" aria-expanded aria-controls={listId} aria-autocomplete="list"
            aria-activedescendant={results[current] ? `${listId}-${current}` : undefined} aria-label="Search pages, entities, or ask a question"
            placeholder={ai ? "Search or ask a question" : "Search pages and entities"} enterKeyHint="go" autoComplete="off"
            value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} />
          <Button variant="link" className="app-search-close" onClick={() => setOpen(false)}>Cancel</Button>
        </div>
        <div id={listId} role="listbox" aria-label="Results" className="app-search-results">
          {groups.map((group) => <section key={group} role="group" aria-label={group}>
            <Heading>{group}</Heading>
            {results.map((r, i) => r.group !== group ? null : (
              <a key={r.id} id={`${listId}-${i}`} data-index={i} role="option" aria-selected={i === current} href={r.to}
                className={i === current ? "on" : undefined} onMouseMove={() => setActive(i)}
                onClick={(e) => { e.preventDefault(); go(r.to); }}>
                {r.group === "Ask" ? <><span className="app-search-ask">Ask</span><span className="app-search-label">{r.label}</span></>
                  : <><span className="app-search-label">{r.label}</span>{r.kind && <KindBadge kind={r.kind} />}</>}
                {r.detail && <span className="app-search-detail">{r.detail}</span>}
              </a>
            ))}
          </section>)}
          {searching && <p className="app-search-note">Searching entities…</p>}
          {hits.error && <div className="app-search-note"><ErrorText>Records search failed: {hits.error}</ErrorText></div>}
          {!searching && q && !results.length && <p className="app-search-note">No matches for “{q}”.</p>}
        </div>
    </div>
  </>);
}
