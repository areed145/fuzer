// The app's navigation: one list of pages, drawn as a grid of app icons in a sidebar (a sheet below 900 px).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { IQ_SPARK, Logo, OPS_IQ } from "./brand";
import { href, refreshAll, useKept, usePhone, useRefreshing } from "./lib";
import { ago } from "./format";
import { Button, Heading } from "./ui";
import { useAuth, usePermission } from "./Auth";
import { goBack, useAppDepth } from "./appHistory";
import { SyncStatus } from "./SyncStatus";
import { AccountButton } from "./Identity";
import { AppSearch, SearchButton } from "./AppSearch";
import { useMenuLabels } from "./theme";

// One count: what waits on you. A data question is a case in the steward's queue, so it is counted there.
export type Badge = "work";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const BADGE_WORDS: Record<Badge, (n: number) => string> = {
  // the List's Waiting on me: events and cases, one each however many steps a case has
  work: (n) => plural(n, "item waiting on you", "items waiting on you"),
};

export interface NavItem {
  label: string;
  /** First path segment the page lives under ("" is the home / browse page). */
  section: string;
  path?: string;
  /** Other first segments that count as this page (entity pages belong to Browse). */
  also?: string[];
  badge?: Badge;
  /** A query key that picks this page out of its section (`#/plan?resources=1` is Resources, not Plan). */
  view?: string;
  /** A link out of the app (opens in a new tab) instead of a page. */
  external?: string;
  /** Desk work a field operator doesn't do: its tab is left out of the phone's row for an account whose only role is
   * operator. Presentation only; the page still opens and its own access rules still apply. */
  desk?: boolean;
  /** Not offered on a phone (the Board needs the width of a tablet or a desk); its link opens the List there. */
  noPhone?: boolean;
  permission?: string;
  ownerOnly?: boolean;
}

export interface NavGroup {
  label: string;
  /** The outline drawn beside the label in the menu (a 24 by 24 path). */
  icon: string;
  items: NavItem[];
  permission?: string;
  demo?: boolean;
}

/** Every page in the nav. Add a page here; nothing else needs to change for it to appear. */
export const NAV: NavGroup[] = [
  // The control tower: the field map with what needs you over it (#/ keeps its URL).
  { label: "Tower", icon: "M12 2v3M4.5 5h15L17 10H7zM10 5.5l.4 4M14 5.5l-.4 4M9.5 10l-1 11M14.5 10l1 11M6 21h12", items: [{ label: "Tower", section: "" }] },
  // Work opens on My work (#1345), what waits on you, for every role and on every device; beside it all the work, as a
  // List (a table) or a Board, one set of rows filtered on any dimension. Master data (#/masterdata) keeps its link
  // and lights Work.
  {
    label: "Work",
    icon: "M6 4h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM8 10l2 2 4-4M8 16h8",
    items: [
      { label: "My work", section: "inbox", also: ["field", "handover", "work"], badge: "work" },
      { label: "List", section: "list",
        also: ["exceptions", "cases", "performance", "teams", "masterdata", "review"] },
      { label: "Board", section: "board", noPhone: true },
    ],
  },
  {
    // The days and weeks ahead, beside Work's today: one timeline for every source, its schedules and its resources.
    label: "Plan",
    icon: "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 9h16M8 2v4M16 2v4M7 13h5M10 17h7",
    items: [
      { label: "Plan", section: "plan", also: ["turnaround"] },
      { label: "Portfolio", section: "plan", path: "/plan?portfolio=1", view: "portfolio" },
      { label: "Schedules", section: "plan", path: "/plan?schedules=1", view: "schedules" },
      { label: "Resources", section: "plan", path: "/plan?resources=1", view: "resources" },
      { label: "Logistics", section: "plan", path: "/plan?logistics=1", view: "logistics" },
      { label: "Backlog by value", section: "ranked" },
    ],
  },
  {
    label: "Explore",
    icon: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15.5 8.5l-2 5-5 2 2-5z",
    // Every entity in the register, and each one's page with everything about it and beneath it. The plant-wide map,
    // network and 3D views keep their links and light Explore; an entity's Map, 3D and Connections tabs show them.
    items: [{ label: "Explore", section: "browse", also: ["e", "map", "network", "3d"] }],
  },
  // Ops IQ, the operations agent, its own destination: it answers from the register, the work and the readings. Beside
  // it, what it knows: the skills its specialists follow, for the engineers who own them, and the facts, lessons and
  // skill drafts waiting for a person (Knowledge), and how well they answer and write (Evals). Asking needs AI access;
  // reading and changing a skill does not, so the group shows for everyone and opens on Skills without it.
  { label: OPS_IQ, icon: IQ_SPARK, items: [
    { label: OPS_IQ, section: "ask", permission: "ai" },
    { label: "Skills", section: "skills", desk: true },
    { label: "Knowledge", section: "knowledge", desk: true },
    { label: "Evals", section: "evals", desk: true },
  ] },
  {
    label: "Reports",
    icon: "M4 20h16M7 16v-5M12 16V7M17 16v-8",
    items: [
      { label: "Reports", section: "reports" },
      { label: "Downtime", section: "downtime" },
      { label: "Incidents", section: "incidents" },
      { label: "Metrics", section: "metrics" },
      { label: "Regulatory", section: "regulatory" },
      { label: "Lessons learned", section: "lessons" },
      { label: "Studies", section: "studies" },
    ],
  },
  { label: "Sources", icon: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6", permission: "configure",
    // The ontology is the model sources map onto, so it sits beside them; #/ontology keeps its URL.
    items: [{ label: "Sources", section: "sources" }, { label: "Ontology", section: "ontology", permission: "configure" }] },
  { label: "Jobs", icon: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2", permission: "configure", items: [{ label: "Jobs", section: "jobs" }] },
  {
    label: "Settings",
    icon: "M4 7h10M18 7h2M4 17h2M10 17h10M16 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM8 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
    items: [
      { label: "Application settings", section: "settings" },
      { label: "Users", section: "users", also: ["access"], permission: "configure" },
      { label: "Routing", section: "routing" },
      // The playbooks the work follows are configuration, beside who gets what; #/processes keeps its URLs.
      { label: "Workflows", section: "workflows", also: ["processes"], desk: true },
      { label: "Docs", section: "docs", external: "https://areed145.github.io/fuzer/" },
    ],
  },
];

export const isActive = (item: NavItem, section: string) =>
  item.section === section || !!item.also?.includes(section);

export function useNavigationAccess() {
  const session = useAuth();
  const configure = usePermission("configure"), ai = usePermission("ai");
  return (item: { permission?: string; ownerOnly?: boolean }) =>
    (!item.ownerOnly || !!session?.user?.owner) &&
    (item.permission !== "configure" || configure) && (item.permission !== "ai" || ai);
}

/** The page a route is on within its group: a page with a `view` wins when its query key is there. */
export const currentItem = (items: NavItem[], section: string, query?: URLSearchParams) =>
  items.find((i) => i.view && isActive(i, section) && query?.has(i.view)) ?? items.find((i) => !i.view && isActive(i, section));

export function AreaNavigation({ section, query, demoEnabled, roles }: {
  section: string; query?: URLSearchParams; demoEnabled: boolean;
  /** The person's workflow roles. An operator and nothing else is in the field: desk tabs leave the phone's row. */
  roles?: string[];
}) {
  const allowed = useNavigationAccess();
  const phone = usePhone();
  const found = navItemFor(section);
  if (!found || !allowed(found.group) || (found.group.demo && !demoEnabled)) return null;
  const items = found.group.items.filter((item) => allowed(item) && !(phone && item.noPhone));
  if (items.length < 2) return null;
  const current = currentItem(items, section, query);
  const field = roles?.length === 1 && roles[0] === "operator";
  return <nav className="area-navigation" aria-label={`${found.group.label} pages`}>
    {items.map((item) => <a key={item.label} href={item.external ?? href(item.path ?? `/${item.section}`)}
      className={field && item.desk && item !== current ? "desk-tab" : undefined}
      aria-current={item === current ? "page" : undefined}
      {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}>{item.label}</a>)}
  </nav>;
}

/** The nav item a route belongs to, for active state and the Back target. */
export function navItemFor(section: string): { group: NavGroup; item: NavItem } | undefined {
  for (const group of NAV) {
    const item = group.items.find((i) => isActive(i, section));
    if (item) return { group, item };
  }
}

/** Where Back goes when there is no page behind it in the app (P5.14a): a deep page goes up to the page that owns it
 * (an entity to Browse, a task to My work), a top page goes Home. None at Home itself. */
export function upFor(path: string[]): { to: string; label: string } | undefined {
  const section = path[0] ?? "";
  if (!section) return undefined;
  const found = navItemFor(section);
  const page = found && found.item.path ? found.item.path : found ? `/${found.item.section}` : "/";
  if (found && !found.item.external && (path.length > 1 || section !== found.item.section) && page !== `/${path.join("/")}`)
    return { to: href(page), label: found.item.label };
  return { to: href("/"), label: "Tower" };
}

/** The installed app has no address bar to copy from (P5.14d): share this page's link, or copy it where the phone
 * can't share. */
function ShareButton() {
  const [said, setSaid] = useState("");
  const share = async () => {
    const url = location.href;
    const title = document.querySelector("#main-content h1")?.textContent?.trim() || "Fuzer";
    try {
      if (navigator.share) { await navigator.share({ title, url }); return; }
      await navigator.clipboard.writeText(url);
      setSaid("Link copied");
    } catch (error) {
      if ((error as DOMException)?.name !== "AbortError") setSaid("Could not share this link");
    }
    window.setTimeout(() => setSaid(""), 2500);
  };
  return <>
    <Button className="share-button" aria-label="Share a link to this page" title="Share a link to this page" onClick={share}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" /></svg>
    </Button>
    <span className="share-status" aria-live="polite">{said}</span>
  </>;
}

/** The in-app Back for the installed app, where the browser's own buttons are gone. */
export function BackButton({ path, className }: { path: string[]; className?: string }) {
  const depth = useAppDepth();
  const up = upFor(path);
  if (depth === 0 && !up) return null;
  const label = depth > 0 ? "Back" : `Back to ${up!.label}`;
  return (
    <Button className={["back-button", className].filter(Boolean).join(" ")} aria-label={label} title={label}
      onClick={() => goBack(up?.to ?? href("/"))}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
      <span aria-hidden="true">Back</span>
    </Button>
  );
}

/** The phone's bottom bar: the primary destinations a dispatched operator reaches for, in this order. Same pages and
 * access rules as the sidebar; the drawer behind Menu still holds all of them. */
const BOTTOM: { group: string; tab?: string; section?: string }[] = [
  { group: "Tower" }, { group: "Work" }, { group: "Explore" }, { group: "Reports" },
];

/** True while the on-screen keyboard is up (the visual viewport shrinks well below the window), so the bottom bar
 * gets out of the way of what's being typed. */
function useKeyboardUp() {
  const [up, setUp] = useState(false);
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const check = () => setUp(viewport.height < window.innerHeight * 0.75);
    viewport.addEventListener("resize", check);
    return () => viewport.removeEventListener("resize", check);
  }, []);
  return up;
}

export function Sidebar({ section, path, badges, status, demoEnabled }: {
  /** The whole route, for the phone top bar's Back. */
  path: string[];
  section: string;
  badges: Record<Badge, number>;
  /** The compact status area at the foot of the sidebar (facility, counts, health). */
  status: ReactNode;
  demoEnabled: boolean;
}) {
  const allowed = useNavigationAccess();
  const [open, setOpen] = useState(false);
  const keyboard = useKeyboardUp();
  const drawer = useRef<HTMLElement>(null);
  const menu = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () => Array.from(drawer.current?.querySelectorAll<HTMLElement>("a[href], button, input, select") ?? [])
      .filter((element) => element.getClientRects().length > 0 && !element.matches(":disabled, [tabindex='-1']"));
    focusable()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const resize = () => { if (window.innerWidth > 900) setOpen(false); };
    window.addEventListener("keydown", trap);
    window.addEventListener("resize", resize);
    return () => {
      document.body.style.overflow = overflow;
      window.removeEventListener("keydown", trap);
      window.removeEventListener("resize", resize);
      if (menu.current?.getClientRects().length) menu.current.focus();
    };
  }, [open]);

  // Close the drawer when the route changes or Escape is pressed.
  useEffect(() => {
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("hashchange", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("hashchange", close);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const refreshing = useRefreshing();
  const labels = useMenuLabels();
  const phone = usePhone();
  return (
    <>
      {/* Search at the top left, the mark in the middle, the account at the top right. */}
      <header className="topbar">
        <div className="topbar-start">
          <SearchButton />
          <BackButton path={path} />
        </div>
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
        <span className="topbar-title" aria-hidden="true" />
        <div className="topbar-end">
          <SyncStatus />
          <ShareButton />
          {/* The installed app has no reload and, on iOS, no pull to refresh (P5.14c). Every view reads again in place. */}
          <Button className="refresh-button" aria-label="Refresh this page" title="Refresh this page" aria-busy={refreshing}
            onClick={() => void refreshAll()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5" /></svg>
          </Button>
          <AccountButton />
        </div>
      </header>
      <AppSearch />
      <KeptNotice />
      {/* On a phone the primary destinations sit at the bottom, within thumb reach (P5.14b); Menu opens the rest. */}
      <nav className={keyboard ? "bottombar keyboard-up" : "bottombar"} aria-label="Quick">
        {BOTTOM.map(({ group: label, tab, section: first }) => {
          const group = NAV.find((g) => g.label === label);
          if (!group || !allowed(group)) return null;
          const item = group.items.find((entry) => entry.section === first && allowed(entry)) ?? group.items.find(allowed);
          if (!item) return null;
          // A slot lights anywhere in its group, so the bar always shows where you are (#1131): the Work slot is lit on
          // My work's and Master data's pages too. It is the current page only on the page it opens.
          const here = item.section === section;
          const on = here || group.items.some((entry) => isActive(entry, section));
          const count = group.items.reduce((n, entry) => n + (entry.badge === "work" ? badges.work : 0), 0);
          const name = tab ?? group.label;
          return (
            <a key={label} href={href(item.path ?? `/${item.section}`)} className={on ? "on" : undefined}
              aria-current={here || (on && !tab) ? "page" : on ? "true" : undefined} aria-label={count > 0 ? `${name}, ${BADGE_WORDS.work(count)}` : undefined}>
              <span className="tab-icon">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d={group.icon} /></svg>
                {count > 0 && <span className="count" aria-hidden="true">{count}</span>}
              </span>
              <span>{name}</span>
            </a>
          );
        })}
        <Button
          ref={menu}
          className="menu-button"
          aria-expanded={open}
          aria-controls="sidebar"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen(!open)}
        >
          <span className="tab-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg></span>
          <span>Menu</span>
        </Button>
      </nav>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside ref={drawer} id="sidebar" className={open ? "sidebar open" : "sidebar"}>
        {open && <Button className="drawer-close" onClick={() => setOpen(false)}>Close menu</Button>}
        {/* On a wide screen the account sits at the panel's top right, beside the mark, so the page has the corner. */}
        <div className="sidebar-head">
          <a className="brand" href={href("/")}>
            <Logo />
          </a>
          <span className="sidebar-account"><AccountButton /></span>
        </div>
        <SearchButton wide />
        {/* Every page an app icon in a grid, as on an iPhone's home screen. The names under them are a setting; with
            them off the name is still the link's name, and a tooltip on a wide screen. */}
        <nav aria-label="Main">
          <section className="nav-group">
              <ul className={labels ? "nav-grid" : "nav-grid no-labels"}>
                {NAV.filter((group) => allowed(group) && (!group.demo || demoEnabled)).map((group) => {
                  const item = group.items.find(allowed);
                  if (!item) return null;
                  const on = group.items.some((entry) => isActive(entry, section));
                  const count = group.items.reduce((n, entry) => n + (entry.badge ? badges[entry.badge] : 0), 0);
                  // Say what the number counts (#397): "1 item waiting on you".
                  const counted = group.items.filter((entry) => entry.badge && badges[entry.badge] > 0)
                    .map((entry) => BADGE_WORDS[entry.badge!](badges[entry.badge!])).join(", ");
                  return (
                    <li key={group.label} className={BOTTOM.some((b) => b.group === group.label) ? "on-bottombar" : undefined}>
                      <a
                        href={href(item.path ?? `/${item.section}`)}
                        className={on ? "nav-tile on" : "nav-tile"}
                        aria-current={on ? "page" : undefined}
                        title={labels ? undefined : group.label}
                        {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}
                      >
                        <span className="nav-tile-icon">
                          <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={group.icon} /></svg>
                        </span>
                        <span className={labels ? "nav-tile-label" : "nav-tile-label sr-only"}>{group.label}</span>
                        {count > 0 && <span className="count" title={counted}>
                          <span aria-hidden="true">{count}</span><span className="sr-only">{counted}</span></span>}
                      </a>
                    </li>
                  );
                })}
              </ul>
          </section>
          {/* Below 900 px the bottom bar holds Tower, Work, Explore and Reports, so the sheet's icons are the rest, and
              under them every page by name (#31): the pages inside an area (Map, Incidents, Schedules) are a tap away
              here, not only from the tabs on each area's first page. */}
        </nav>
        <nav className="nav-pages" aria-label="All pages">
            {NAV.filter((group) => allowed(group) && (!group.demo || demoEnabled)).map((group) => {
              const items = group.items.filter((item) => allowed(item) && !(phone && item.noPhone));
              if (items.length < 2) return null;
              const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
              const current = currentItem(items, section, query);
              return (
                <div key={group.label} className="nav-pages-group">
                  <Heading small>{group.label}</Heading>
                  <ul>
                    {items.map((item) => (
                      <li key={item.label}>
                        <a href={item.external ?? href(item.path ?? `/${item.section}`)}
                          aria-current={item === current ? "page" : undefined}
                          {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}>{item.label}</a>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
        </nav>
        <div className="sidebar-status">{status}</div>
      </aside>
    </>
  );
}


/** While a page shows answers kept from an earlier visit and the fresh ones are on their way, say how old they are, so
 * a last-known figure is never taken for a live one. A kept answer under a minute old isn't worth the words. */
function KeptNotice() {
  const since = useKept();
  if (since === null || Date.now() - since < 60_000) return null;
  return <div className="kept-notice" role="status">Showing what was here {ago(new Date(since).toISOString())}. Refreshing…</div>;
}
