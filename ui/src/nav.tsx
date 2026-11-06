// The app's navigation: one list of pages, drawn as a sidebar (a drawer below 900 px).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Logo } from "./brand";
import { href } from "./lib";
import { Button } from "./ui";
import { useAuth, usePermission } from "./Auth";

export type Badge = "work" | "review";

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
  items: NavItem[];
  permission?: string;
  demo?: boolean;
}

/** Every page in the nav. Add a page here; nothing else needs to change for it to appear. */
export const NAV: NavGroup[] = [
  { label: "Home", items: [{ label: "Home", section: "" }] },
  { label: "Demo", demo: true, items: [
    { label: "Demo tour", section: "demo" },
    { label: "Okan investigation", section: "investigate", path: "/investigate/okan" },
  ] },
  {
    label: "Explore",
    items: [
      { label: "Browse", section: "browse", also: ["e"] },
      { label: "Map", section: "map" },
      { label: "Network", section: "network" },
      { label: "Ontology", section: "ontology", permission: "configure" },
    ],
  },
  {
    label: "Investigate",
    items: [
      { label: "Downtime", section: "downtime" },
      { label: "Ask", section: "ask", permission: "ai" },
      { label: "Incidents", section: "incidents" },
      { label: "Daily reports", section: "reports" },
      { label: "Lessons learned", section: "lessons" },
      { label: "Studies", section: "studies" },
    ],
  },
  {
    label: "Work",
    items: [
      { label: "Work board", section: "board" },
      { label: "Inbox", section: "inbox", also: ["work", "teams"], badge: "work" },
      { label: "Cases", section: "cases" },
      { label: "Data review", section: "review", badge: "review" },
      { label: "Processes", section: "processes" },
    ],
  },
  { label: "Sources", permission: "configure", items: [{ label: "Sources", section: "sources" }] },
  { label: "Jobs", permission: "configure", items: [{ label: "Jobs", section: "jobs" }] },
  {
    label: "Settings",
    items: [
      { label: "Application settings", section: "settings" },
      { label: "Users", section: "users", also: ["access"], permission: "configure" },
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

export function Sidebar({ section, badges, status, demoEnabled }: {
  section: string;
  badges: Record<Badge, number>;
  /** The compact status area at the foot of the sidebar (facility, counts, health). */
  status: ReactNode;
  demoEnabled: boolean;
}) {
  const allowed = useNavigationAccess();
  const [open, setOpen] = useState(false);
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
        <Button
          ref={menu}
          className="menu-button"
          aria-expanded={open}
          aria-controls="sidebar"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen(!open)}
        >
          <span aria-hidden="true">{open ? "✕" : "☰"}</span>
        </Button>
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
      </header>
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
                  return (
                    <li key={group.label}>
                      <a
                        href={href(item.path ?? `/${item.section}`)}
                        className={on ? "on" : undefined}
                        aria-current={on ? "page" : undefined}
                        {...(item.external ? { target: "_blank", rel: "noreferrer" } : {})}
                      >
                        <span>{group.label}</span>
                        {count > 0 && <span className="count">{count}</span>}
                      </a>
                    </li>
                  );
                })}
              </ul>
          </section>
        </nav>
        <div className="sidebar-status">{status}</div>
        <a className="small" href={href("/settings")}>Appearance & settings</a>
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
  else if (section !== "investigate" || path[1] !== "okan") return null;
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
