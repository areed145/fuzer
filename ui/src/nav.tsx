// The app's navigation: one list of pages, drawn as a sidebar (a drawer below 900 px).
import { useEffect, useState, type ReactNode } from "react";
import { Logo } from "./brand";
import { href } from "./lib";

export type Badge = "work" | "review";

export interface NavItem {
  label: string;
  /** First path segment the page lives under ("" is the home / browse page). */
  section: string;
  /** Other first segments that count as this page (entity pages belong to Browse). */
  also?: string[];
  badge?: Badge;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/** Every page in the nav. Add a page here; nothing else needs to change for it to appear. */
export const NAV: NavGroup[] = [
  {
    label: "Explore",
    items: [
      { label: "Browse", section: "", also: ["e"] },
      { label: "Map", section: "map" },
      { label: "Network", section: "network" },
      { label: "Ontology", section: "ontology" },
    ],
  },
  {
    label: "Operations",
    items: [
      { label: "Downtime", section: "downtime" },
      { label: "Incidents", section: "incidents" },
      { label: "Reports", section: "reports" },
    ],
  },
  {
    label: "Knowledge",
    items: [
      { label: "Ask", section: "ask" },
      { label: "Lessons", section: "lessons" },
      { label: "Studies", section: "studies" },
    ],
  },
  {
    label: "Work",
    items: [
      { label: "Board", section: "board" },
      { label: "Work", section: "work", badge: "work" },
      { label: "Review", section: "review", badge: "review" },
    ],
  },
  {
    label: "Admin",
    items: [
      { label: "Sources", section: "sources" },
      { label: "Settings", section: "settings" },
      { label: "Architecture", section: "architecture" },
    ],
  },
];

export const isActive = (item: NavItem, section: string) =>
  item.section === section || !!item.also?.includes(section);

/** The nav item a route belongs to, for active state and breadcrumbs. */
export function navItemFor(section: string): { group: NavGroup; item: NavItem } | undefined {
  for (const group of NAV) {
    const item = group.items.find((i) => isActive(i, section));
    if (item) return { group, item };
  }
}

export function Sidebar({ section, badges, status }: {
  section: string;
  badges: Record<Badge, number>;
  /** The compact status area at the foot of the sidebar (facility, counts, health). */
  status: ReactNode;
}) {
  const [open, setOpen] = useState(false);

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
        <button
          className="menu-button"
          aria-expanded={open}
          aria-controls="sidebar"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen(!open)}
        >
          <span aria-hidden="true">{open ? "✕" : "☰"}</span>
        </button>
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
      </header>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside id="sidebar" className={open ? "sidebar open" : "sidebar"}>
        <a className="brand" href={href("/")}>
          <Logo />
        </a>
        <nav aria-label="Main">
          {NAV.map((group) => (
            <section key={group.label} className={group.label === "Admin" ? "nav-group admin" : "nav-group"}>
              <h2 id={`nav-${group.label}`}>{group.label}</h2>
              <ul aria-labelledby={`nav-${group.label}`}>
                {group.items.map((item) => {
                  const on = isActive(item, section);
                  const count = item.badge ? badges[item.badge] : 0;
                  return (
                    <li key={item.label}>
                      <a
                        href={href(`/${item.section}`)}
                        className={on ? "on" : undefined}
                        aria-current={on ? "page" : undefined}
                      >
                        <span>{item.label}</span>
                        {count > 0 && <span className="count">{count}</span>}
                      </a>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
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
  const trail: { label: string; to?: string }[] = [{ label: found.item.label, to: `/${found.item.section}` }];
  if (found.group.label !== found.item.label) trail.unshift({ label: found.group.label });
  if (section === "e") trail.push({ label: "Entity" });
  else if (section === "work" && path[1] === "item") trail.push({ label: `Item ${path[2] ?? ""}`.trim() });
  else if (section === "work" && path[1] === "processes") {
    trail.push({ label: "Processes", to: path[2] ? "/work/processes" : undefined });
    if (path[2]) trail.push({ label: path[2] });
  } else if (section === "work" && path[1] === "forms") {
    trail.push({ label: "Forms" });
    if (path[2]) trail.push({ label: path[2] });
  } else return null;
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
