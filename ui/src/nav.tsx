// The app's navigation: one list of pages, drawn as a sidebar (a drawer below 900 px).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Logo } from "./brand";
import { href } from "./lib";
import { Button } from "./ui";
import { useAuth, usePermission } from "./Auth";
import { goBack, useAppDepth } from "./appHistory";

export type Badge = "work" | "review";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const BADGE_WORDS: Record<Badge, (n: number) => string> = {
  work: (n) => plural(n, "workflow step waiting on you", "workflow steps waiting on you"),
  review: (n) => plural(n, "data review", "data reviews"),
};

export interface NavItem {
  label: string;
  /** First path segment the page lives under ("" is the home / browse page). */
  section: string;
  path?: string;
  /** Other first segments that count as this page (entity pages belong to Browse). */
  also?: string[];
  badge?: Badge;
  /** A link out of the app (opens in a new tab) instead of a page. */
  external?: string;
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
  { label: "Home", icon: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z", items: [{ label: "Home", section: "" }] },
  // One entry: the tour starts the guided walkthrough, which keeps its #/investigate/okan URL (#556).
  { label: "Demo", icon: "M6 4l14 8-14 8z", demo: true, items: [{ label: "Demo tour", section: "demo", also: ["investigate"] }] },
  {
    label: "Explore",
    icon: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15.5 8.5l-2 5-5 2 2-5z",
    items: [
      { label: "Browse", section: "browse", also: ["e"] },
      { label: "Map", section: "map" },
      { label: "Network", section: "network" },
      { label: "3D", section: "3d" },
    ],
  },
  {
    label: "Investigate",
    icon: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM16.5 16.5L21 21",
    items: [
      { label: "Downtime", section: "downtime" },
      { label: "Ask", section: "ask", permission: "ai" },
      { label: "Incidents", section: "incidents" },
      { label: "Daily reports", section: "reports" },
      { label: "Metrics", section: "metrics", also: ["scorecard"] },
      { label: "Lessons learned", section: "lessons" },
      { label: "Studies", section: "studies" },
    ],
  },
  {
    label: "Work",
    icon: "M6 4h12a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM8 10l2 2 4-4M8 16h8",
    items: [
      { label: "All work", section: "exceptions", also: ["board", "cases", "performance"] },
      // Handover is reached from My work, Turnaround is a window on Plan, Data review from Master data (#554).
      { label: "My work", section: "inbox", also: ["work", "teams", "field", "handover"], badge: "work" },
      { label: "Plan", section: "plan", also: ["turnaround"] },
      { label: "Master data", section: "masterdata", also: ["review"], badge: "review" },
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
      { label: "Processes", section: "processes" },
      { label: "Docs", section: "docs", external: "https://areed145.github.io/fuzer/" },
    ],
  },
];

export const isActive = (item: NavItem, section: string) =>
  item.section === section || !!item.also?.includes(section);

function useNavigationAccess() {
  const session = useAuth();
  const configure = usePermission("configure"), ai = usePermission("ai");
  return (item: { permission?: string; ownerOnly?: boolean }) =>
    (!item.ownerOnly || !!session?.user?.owner) &&
    (item.permission !== "configure" || configure) && (item.permission !== "ai" || ai);
}

export function AreaNavigation({ section, demoEnabled }: { section: string; demoEnabled: boolean }) {
  const allowed = useNavigationAccess();
  const found = navItemFor(section);
  if (!found || !allowed(found.group) || (found.group.demo && !demoEnabled)) return null;
  const items = found.group.items.filter(allowed);
  if (items.length < 2) return null;
  return <nav className="area-navigation" aria-label={`${found.group.label} pages`}>
    {items.map((item) => <a key={item.section} href={item.external ?? href(item.path ?? `/${item.section}`)}
      aria-current={isActive(item, section) ? "page" : undefined}
      {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}>{item.label}</a>)}
  </nav>;
}

/** The nav item a route belongs to, for active state and breadcrumbs. */
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
  return { to: href("/"), label: "Home" };
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
  { group: "Home" }, { group: "Work", tab: "My work", section: "inbox" }, { group: "Explore" }, { group: "Investigate" },
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

  return (
    <>
      <header className="topbar">
        <BackButton path={path} />
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
        <ShareButton />
        {/* The installed app has no reload and, on iOS, no pull to refresh (P5.14c). It reloads this page, in place. */}
        <Button className="refresh-button" aria-label="Refresh this page" title="Refresh this page" onClick={() => location.reload()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5" /></svg>
        </Button>
      </header>
      {/* On a phone the primary destinations sit at the bottom, within thumb reach (P5.14b); Menu opens the rest. */}
      <nav className={keyboard ? "bottombar keyboard-up" : "bottombar"} aria-label="Quick">
        {BOTTOM.map(({ group: label, tab, section: first }) => {
          const group = NAV.find((g) => g.label === label);
          if (!group || !allowed(group)) return null;
          const item = group.items.find((entry) => entry.section === first && allowed(entry)) ?? group.items.find(allowed);
          if (!item) return null;
          const on = group.items.some((entry) => isActive(entry, section));
          const count = group.items.reduce((n, entry) => n + (entry.badge === "work" ? badges.work : 0), 0);
          const name = tab ?? group.label;
          return (
            <a key={label} href={href(item.path ?? `/${item.section}`)} className={on ? "on" : undefined}
              aria-current={on ? "page" : undefined} aria-label={count > 0 ? `${name}, ${BADGE_WORDS.work(count)}` : undefined}>
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
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
        <nav aria-label="Main">
          <section className="nav-group">
              <ul>
                {NAV.filter((group) => allowed(group) && (!group.demo || demoEnabled)).map((group) => {
                  const item = group.items.find(allowed);
                  if (!item) return null;
                  const on = group.items.some((entry) => isActive(entry, section));
                  const count = group.items.reduce((n, entry) => n + (entry.badge ? badges[entry.badge] : 0), 0);
                  // Say what the number counts (#397): "1 workflow step waiting, 14 data reviews".
                  const counted = group.items.filter((entry) => entry.badge && badges[entry.badge] > 0)
                    .map((entry) => BADGE_WORDS[entry.badge!](badges[entry.badge!])).join(", ");
                  return (
                    <li key={group.label}>
                      <a
                        href={href(item.path ?? `/${item.section}`)}
                        className={on ? "on" : undefined}
                        aria-current={on ? "page" : undefined}
                        {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}
                      >
                        <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={group.icon} /></svg>
                        <span>{group.label}</span>
                        {count > 0 && <span className="count" title={counted}>
                          <span aria-hidden="true">{count}</span><span className="sr-only">{counted}</span></span>}
                      </a>
                    </li>
                  );
                })}
              </ul>
          </section>
        </nav>
        <div className="sidebar-status">{status}</div>
      </aside>
    </>
  );
}

/** "Explore › Browse › …" above deep pages (an entity, a work item, a process). */
export function Breadcrumbs({ path }: { path: string[] }) {
  const section = path[0] ?? "";
  const found = navItemFor(section);
  if (!found || path.length < 2) return null;
  const trail: { label: string; to?: string }[] = [{ label: found.item.label, to: found.item.path ?? `/${found.item.section}` }];
  if (found.group.label !== found.item.label) trail.unshift({ label: found.group.label });
  if (section === "e") trail.push({ label: "Entity" });
  else if (section === "jobs" && path[1] === "run") trail.push({ label: `Run ${(path[2] ?? "").slice(0, 8)}`.trim() });
  else if (section === "inbox" && path[1] === "task") trail.push({ label: `Task ${path[2] ?? ""}`.trim() });
  else if (section === "cases" && path[1]) trail.push({ label: `Case ${path[1]}` });
  else if (section === "processes" && path[1] === "forms") {
    trail.push({ label: "Forms" });
    if (path[2]) trail.push({ label: path[2] });
  } else if (section === "processes" && path[1]) trail.push({ label: path[1] });
  else if (section === "investigate" && path[1] === "okan") trail.push({ label: "Guided investigation" });
  else return null;
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          return (
            <li key={i}>
              {c.to && !last ? <a href={href(c.to)}>{c.label}</a> : <span aria-current={last ? "page" : undefined}>{c.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
