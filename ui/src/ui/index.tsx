import "../styles/ui-kit.css";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, LabelHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes, TableHTMLAttributes, TextareaHTMLAttributes } from "react";
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { lockPageScroll } from "../scrollLock";

export function ActionStatus({ busy, error, children }: { busy?: string | null; error?: string | null; children?: ReactNode }) {
  return <>{busy && <p role="status">{busy}</p>}
    {error && <p role="alert" className="error">{error} Check what was saved before trying again.</p>}
    {!busy && !error && children && <div role="status">{children}</div>}</>;
}

export function ConfirmAction({ label, consequence, busy, disabled, onConfirm }: {
  label: string; consequence: string; busy?: boolean; disabled?: boolean; onConfirm: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);
  // On a view-only page (the shell's PageNote says so) a change the server would refuse isn't offered at all.
  const viewOnly = useContext(PageNote) != null;
  useEffect(() => {
    if (!confirming && wasConfirming.current) opener.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);
  if (viewOnly) return null;
  return confirming ? <div className="notice" role="group" aria-label={`Confirm ${label}`}>
    <p>{consequence}</p>
    <Button type="button" variant="primary" disabled={busy || disabled} onClick={() => { setConfirming(false); onConfirm(); }}>Confirm {label}</Button>{" "}
    <Button type="button" autoFocus disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
  </div> : <Button ref={opener} type="button" variant="primary" disabled={busy || disabled} onClick={() => setConfirming(true)}>{label}</Button>;
}

type Width = "xs" | "sm" | "md" | "lg" | "full";


/** The page title and the page's actions. `detail` is a fact about the record the page shows (its description, its
 * dates, its version), never a line about what the page is for: a title needs no tagline. `help` names a docs page
 * (for example "workflow") and adds a Help link, which is where explanations of the page belong. */
/** A standing note about the whole page (the demo's "View only"). The shell provides it and the page header shows it
 * under the title, so the title stays the first thing on the page. */
export const PageNote = createContext<ReactNode>(null);

/** The page's heading. A page's name is not shown: the lit tab already says where you are, and the top bar shows the
 * Fuzer mark. The h1 stays for screen readers and focus. Pass `record` when the heading names the record on screen
 * (a case, an asset, a dataset), which the tab cannot say. */
export function PageHeader({ children, detail, actions, className = "", record = false }: {
  children: ReactNode; detail?: ReactNode; actions?: ReactNode; className?: string; record?: boolean;
}) {
  const tools = actions && <div className="ui-page-actions">{actions}</div>;
  const note = useContext(PageNote);
  const heading = <h1 tabIndex={-1} className={`${className}${record ? "" : " sr-only"}`.trim() || undefined}>{children}</h1>;
  if (!record && !detail && !tools) return <>{heading}{note}</>;
  return <><header className="ui-page-header"><div>{heading}{detail && <p className="ui-page-subtitle">{detail}</p>}</div>{tools}</header>{note}</>;
}

/** A message the user needs to act on. Not for explanations or disclaimers. */
export function Notice({ tone = "info", className = "", ...props }: HTMLAttributes<HTMLDivElement> & { tone?: "info" | "warn" | "danger" }) {
  return <div {...props} className={`notice ui-notice-${tone} ${className}`.trim()} />;
}

/** One row of views of one page (a segmented control). An item with `href` is a link and the current one carries
 * aria-current; an item with `onSelect` switches state in place and the current one is pressed. `small` is the compact
 * row used inside a card. */
export interface TabItem { key: string | number; label: ReactNode; current?: boolean; href?: string; onSelect?: () => void; title?: string }
export function Tabs({ items, label, small, className = "" }: { items: TabItem[]; label: string; small?: boolean; className?: string }) {
  const cls = `tabs${small ? " small" : ""} ${className}`.trim();
  // A row too wide for the screen scrolls sideways; it opens with the current view in sight.
  const row = useRef<HTMLElement & HTMLDivElement>(null);
  const current = items.find((item) => item.current)?.key;
  useEffect(() => {
    const el = row.current, on = el?.querySelector<HTMLElement>(".on");
    if (el && on && el.scrollWidth > el.clientWidth) el.scrollLeft = on.offsetLeft - (el.clientWidth - on.offsetWidth) / 2;
  }, [current]);
  const links = items.some((item) => item.href !== undefined);
  const tabs = items.map((item) => item.href !== undefined
    ? <a key={item.key} href={item.href} title={item.title} className={item.current ? "on" : undefined}
        aria-current={item.current ? "page" : undefined} onClick={item.onSelect}>{item.label}</a>
    : <Button key={item.key} type="button" variant="ghost" title={item.title} className={item.current ? "on" : undefined}
        aria-pressed={!!item.current} onClick={item.onSelect}>{item.label}</Button>);
  return links ? <nav ref={row} className={cls} aria-label={label}>{tabs}</nav>
    : <div ref={row} className={cls} role="group" aria-label={label}>{tabs}</div>;
}

/** Every row that scrolls sideways: the kit's tabs, area navigation, chip rows, Home's figures and the like. A row
 * wider than the screen shows that there is more by fading the edge that has more and snapping so items rest whole,
 * only the next one peeking in. `watchScrollers` marks every such row once, so a page adds nothing; a new row uses
 * `Scroller`, or one of these classes. */
export const SCROLL_ROWS = ".ui-scroller, .tabs, .area-navigation, .chips, .report-days, .map-legend, .trend-controls";

function markScroll(el: HTMLElement) {
  const max = el.scrollWidth - el.clientWidth, at = Math.abs(el.scrollLeft);
  // A few pixels either way is a row's own padding settling under the snap, not more to scroll to.
  const more = max <= 4 ? "" : at <= 4 ? "end" : at >= max - 4 ? "start" : "both";
  if ((el.dataset.more ?? "") !== more) { if (more) el.dataset.more = more; else delete el.dataset.more; }
}

export function watchScrollers() {
  const seen = new WeakSet<Element>();
  const sizes = new ResizeObserver((entries) => entries.forEach((e) => markScroll(e.target as HTMLElement)));
  let queued = false;
  const scan = () => {
    queued = false;
    document.querySelectorAll<HTMLElement>(SCROLL_ROWS).forEach((el) => {
      if (!seen.has(el)) {
        seen.add(el);
        el.addEventListener("scroll", () => markScroll(el), { passive: true });
        sizes.observe(el);
      }
      markScroll(el);  // its items may have changed without its own box changing size
    });
  };
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(scan); } })
    .observe(document.body, { childList: true, subtree: true });
  scan();
}

/** A row of cards, chips or buttons that scrolls sideways on a narrow screen (see SCROLL_ROWS). */
export function Scroller({ label, className = "", children }: { label?: string; className?: string; children: ReactNode }) {
  // A row cut off at an edge fades there, so a chip clipped mid-word reads as "more this way" (#19), not a bug.
  const el = useRef<HTMLDivElement>(null);
  const [more, setMore] = useState({ start: false, end: false });
  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const check = () => {
      const start = node.scrollLeft > 1, end = node.scrollLeft + node.clientWidth < node.scrollWidth - 1;
      setMore((m) => (m.start === start && m.end === end ? m : { start, end }));
    };
    check();
    node.addEventListener("scroll", check, { passive: true });
    const seen = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(check);
    seen?.observe(node);
    return () => { node.removeEventListener("scroll", check); seen?.disconnect(); };
  }, []);
  return <div ref={el} className={`ui-scroller ${className}`.trim()} role={label ? "group" : undefined} aria-label={label}
    data-more={more.start && more.end ? "both" : more.start ? "start" : more.end ? "end" : undefined}>{children}</div>;
}

/** A heading for a part of a page or a group inside a card, where it isn't a Card's own title. `detail` is the grey
 * fact after it (a count, a date, an id), `actions` the controls on its right, and `small` the compact heading of a
 * group inside a card. */
export function Heading({ level = 2, small, mono, detail, actions, id, tabIndex, ref, className = "", children }: {
  level?: 2 | 3 | 4; small?: boolean; mono?: boolean; detail?: ReactNode; actions?: ReactNode; id?: string; tabIndex?: number;
  ref?: Ref<HTMLHeadingElement>; className?: string; children: ReactNode;
}) {
  const Tag = `h${level}` as "h2" | "h3" | "h4";
  const cls = `${small ? "small" : ""} ${mono ? "mono" : ""} ${className}`.trim() || undefined;
  const heading = <Tag ref={ref} id={id} tabIndex={tabIndex} className={cls}>
    {children}{detail !== undefined && detail !== null && detail !== false && detail !== "" && <> <Text small muted>{detail}</Text></>}
  </Tag>;
  return actions ? <header className="ui-card-header">{heading}{actions}</header> : heading;
}

export function Button({ variant = "secondary", size = "md", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { ref?: Ref<HTMLButtonElement>;
  variant?: "primary" | "secondary" | "ghost" | "link" | "danger"; size?: "sm" | "md";
}) {
  return <button {...props} className={`ui-button ${`ui-button-${variant}`} ui-button-${size} ${className}`.trim()} />;
}

export function ButtonLink({ variant = "secondary", size = "md", className = "", ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: "primary" | "secondary" | "ghost" | "link" | "danger"; size?: "sm" | "md";
}) {
  return <a {...props} className={`ui-button ui-button-${variant} ui-button-${size} ${className}`.trim()} />;
}

export function ButtonLabel({ variant = "secondary", size = "md", className = "", ...props }: LabelHTMLAttributes<HTMLLabelElement> & {
  variant?: "primary" | "secondary" | "ghost" | "link" | "danger"; size?: "sm" | "md";
}) {
  return <label {...props} className={`ui-button ui-button-${variant} ui-button-${size} ${className}`.trim()} />;
}

/** A card. `title` is its heading, `detail` the grey words after it, `actions` the controls on the heading's right. */
export function Card({ as: Component = "div", title, detail, actions, children, className = "", ...props }: Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  as?: "div" | "section" | "aside" | "figure"; title?: ReactNode; detail?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
}) {
  return <Component {...props} className={`ui-card card ${className}`.trim()}>
    {(title || actions) && <header className="ui-card-header">
      {title && <h2>{title}{detail !== undefined && detail !== null && detail !== false && detail !== "" && <> <Text small muted>{detail}</Text></>}</h2>}{actions}
    </header>}
    {children}
  </Component>;
}
export const Section = Card;

/** On a phone a `phone-rows` table stacks each row (styles/work.css), so a cell can't lean on its column heading any more:
 * each cell is labelled with its heading, unless it names its own `data-label`. The first column is the row's title and
 * needs none. Rows added later are labelled too. */
const NUMBER = /^[-+−]?[$€£]?\d[\d,. ]*(%|\s?[A-Za-z³/]{1,6})?$/;

function labelCells(table: HTMLTableElement) {
  const heads = Array.from(table.tHead?.rows[0]?.cells ?? []).map((th) => th.textContent?.trim() ?? "");
  if (!heads.length) return;
  for (const body of Array.from(table.tBodies)) for (const row of Array.from(body.rows)) {
    let column = 0;
    for (const cell of Array.from(row.cells)) {
      const head = heads[column];
      if (column > 0 && head && !cell.hasAttribute("data-label")) cell.setAttribute("data-label", head);
      // A cell that holds only a number (with an optional unit or %) lines up on the right in tabular figures.
      cell.classList.toggle("ui-num", column > 0 && NUMBER.test(cell.textContent?.trim() ?? ""));
      // A cell with nothing to say (empty, or only a dash) drops out of a stacked phone row instead of showing "—".
      cell.classList.toggle("ui-blank", column > 0 && !cell.querySelector("input, button, select, textarea, a, img, svg, canvas") && /^[—–-]?$/.test(cell.textContent?.trim() ?? ""));
      column += cell.colSpan || 1;
    }
  }
}

export function Table({ children, className = "", variant = "default", density = "normal", clickable = variant === "results", headingCase = "capitalize", ...props }: TableHTMLAttributes<HTMLTableElement> & {
  className?: string; variant?: "default" | "results" | "keyValue" | "list" | "compare" | "settings" | "mapping";
  density?: "normal" | "compact"; clickable?: boolean; headingCase?: "capitalize" | "verbatim";
}) {
  const ref = useRef<HTMLTableElement>(null);
  // Every list-like table stacks into list rows on a phone (one look across pages); key-value, comparison, settings and
  // mapping tables keep their own phone layouts.
  const stacks = /\bphone-rows\b/.test(className) || variant === "default" || variant === "results" || variant === "list";
  useEffect(() => {
    const table = ref.current;
    if (!stacks || !table) return;
    labelCells(table);
    const watch = new MutationObserver(() => labelCells(table));
    watch.observe(table, { childList: true, subtree: true });
    return () => watch.disconnect();
  }, [stacks]);
  return <div className="ui-table-wrap"><table ref={ref} {...props} className={`ui-table ui-table--${variant} ui-table-${density}${clickable ? " ui-table-clickable" : ""} ui-table-heading-${headingCase}${stacks ? " phone-stack" : ""} ${className}`.trim()}>{children}</table></div>;
}

export function Field({ label, hint, width, inline, children }: { label: ReactNode; hint?: ReactNode; width?: Width; inline?: boolean; children: ReactNode }) {
  return <label className={inline ? `ui-field ui-field-inline${width ? ` ui-width-${width}` : ""}` : `ui-field ui-width-${width ?? "md"}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
export function Input({ width, className = "", ...props }: InputHTMLAttributes<HTMLInputElement> & { width?: Width; ref?: Ref<HTMLInputElement> }) {
  return <input {...props} className={`ui-input ${width ? `ui-width-${width}` : ""} ${className}`.trim()} />;
}
/** Longer text. The same field as `Input`; give it `rows`, and `width="full"` to span its container. */
export function Textarea({ width, className = "", ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { width?: Width; ref?: Ref<HTMLTextAreaElement> }) {
  return <textarea {...props} className={`ui-textarea ${width ? `ui-width-${width}` : ""} ${className}`.trim()} />;
}
export function Select({ width, className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement> & { width?: Width }) {
  return <select {...props} className={`ui-input ${width ? `ui-width-${width}` : ""} ${className}`.trim()} />;
}
/** A list. `plain` drops the bullets; `small` and `muted` are the kit's text styles. */
export function List({ as: Tag = "ul", plain, small, muted, ...props }: Omit<HTMLAttributes<HTMLElement>, "className"> & {
  as?: "ul" | "ol"; plain?: boolean; small?: boolean; muted?: boolean;
}) {
  return <Tag {...props} className={[plain && "plain", small && "small", muted && "muted"].filter(Boolean).join(" ") || undefined} />;
}
/** A checkbox with its label. `onChange` gets the new state. */
export function Checkbox({ checked, onChange, disabled, children }: {
  checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; children: ReactNode;
}) {
  return <label className="ui-check"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {children}</label>;
}
export type Tone = "ok" | "warn" | "danger" | "info" | "muted";

/** A status word in the colour of its state. No class names: the tone is the whole look. */
export function Badge({ tone = "muted", mono, ...props }: Omit<HTMLAttributes<HTMLSpanElement>, "className"> & { tone?: Tone; mono?: boolean }) {
  return <span {...props} className={`ui-badge ui-badge-${tone}${mono ? " mono" : ""}`} />;
}
/** What went wrong, in the one error style and announced to screen readers. Renders nothing without a message. Use it
 * instead of a paragraph with an error class. */
export function ErrorText({ children, inline = false }: { children?: ReactNode; inline?: boolean }) {
  if (!children) return null;
  const Tag = inline ? "span" : "p";
  return <Tag role="alert" className="ui-error">{children}</Tag>;
}
const classes = (flags: Record<string, boolean | undefined>) => Object.keys(flags).filter((name) => flags[name]).join(" ") || undefined;

/** Text in one of the app's few text styles: `small`, `muted` (grey), `mono` (ids and codes), in any mix. A span unless
 * `as` says a paragraph or a block. It takes no class name: a style that isn't here is added here, for every page. */
export function Text({ as: Tag = "span", small, muted, mono, ...props }: Omit<HTMLAttributes<HTMLElement>, "className"> & {
  as?: "span" | "p" | "div"; small?: boolean; muted?: boolean; mono?: boolean;
}) {
  return <Tag {...props} className={classes({ small, muted, mono })} />;
}

/** Things on one line with the app's gap: `spread` pushes them to the two ends, `wrap` lets them wrap. */
export function Line({ as: Tag = "div", spread, wrap, small, muted, ...props }: Omit<HTMLAttributes<HTMLElement>, "className"> & {
  as?: "div" | "span" | "p"; spread?: boolean; wrap?: boolean; small?: boolean; muted?: boolean;
}) {
  return <Tag {...props} className={classes({ row: true, spread, wrap, small, muted })} />;
}
/** A labelled figure. With `href` the whole figure links to the view that shows the same number; `tone="danger"` marks
 * one that needs acting on (work waiting to dispatch). */
export function Stat({ label, value, detail, href, tone }: { label: ReactNode; value: ReactNode; detail?: ReactNode; href?: string;
                                                          tone?: "danger" }) {
  const className = `ui-stat${href ? " ui-stat-link" : ""}${tone ? ` ui-stat-${tone}` : ""}`;
  const body = <><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</>;
  return href ? <a className={className} href={href}>{body}</a> : <div className={className}>{body}</div>;
}
/** A row of figures: wrap `Stat`s in it. On the page each is a tile; inside a card they sit plain. */
export function Stats({ children }: { children: ReactNode }) {
  return <div className="ui-stats">{children}</div>;
}
/** Facts about one thing, each a name and its value. One per line by default; `grid` sets them side by side with the
 * name over the value. A falsy item is left out, so `cond && ["Method", m]` works. */
export function Facts({ items, grid }: { items: (readonly [ReactNode, ReactNode] | false | null | undefined | "")[]; grid?: boolean }) {
  const shown = items.filter((item): item is readonly [ReactNode, ReactNode] => !!item);
  if (!shown.length) return null;
  return <dl className={grid ? "ui-facts ui-facts-grid" : "ui-facts"}>
    {shown.map(([name, value], i) => <div key={i}><dt>{name}</dt><dd>{value}</dd></div>)}
  </dl>;
}
/** Empty means one line and at most one action (docs/ui-style.md, rule 8): "No incidents in this window." */
export function Empty({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <p className={`ui-empty-line ${className}`.trim()}>{children}</p>;
}
export function EmptyState({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return <div className="ui-empty"><strong>{title}</strong>{children && <div>{children}</div>}</div>;
}
export function Bar({ value, label, className = "" }: { value: number; label?: string; className?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return <span className={`ui-bar ${className}`.trim()} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
    <span style={{ width: `${pct}%` }} />
  </span>;
}

/** Content shown on request: a plain text button, no caret. Use it for long data (a table, a stack trace, a history),
 * never for guidance, which belongs in the docs. `open` sets the first state only. */
export function Disclosure({ label, open = false, className = "", children }: {
  label: ReactNode; open?: boolean; className?: string; children?: ReactNode;
}) {
  const [shown, setShown] = useState(open);
  const id = useId();
  return <div className={`ui-disclosure ${className}`.trim()}>
    <button type="button" className="ui-disclosure-toggle" aria-expanded={shown} aria-controls={id} onClick={() => setShown(!shown)}>{label}</button>
    {shown && <div id={id} className="ui-disclosure-body">{children}</div>}
  </div>;
}

/** A view (a chart, a drawing, a map, the 3D scene) on the whole screen. The browser's own full screen where it
 * allows one; elsewhere (an iPhone, which has none for anything but video) the view covers the page instead. Either
 * way the view adds `ui-fullscreen` to the element while `on`, Escape leaves, and `on` lets it redraw at its new
 * size. */
export function useFullscreen<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T>(null);
  const [native, setNative] = useState(false);
  const [covering, setCovering] = useState(false);
  const on = native || covering;
  useEffect(() => {
    const changed = () => setNative(!!ref.current && document.fullscreenElement === ref.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (!covering) return;
    const unlock = lockPageScroll("ui-fullscreen-open");
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setCovering(false); };
    window.addEventListener("keydown", key);
    return () => {
      unlock();
      window.removeEventListener("keydown", key);
    };
  }, [covering]);
  const toggle = async () => {
    const el = ref.current;
    if (!el) return;
    if (document.fullscreenElement === el) return void await document.exitFullscreen().catch(() => {});
    if (covering) return setCovering(false);
    try {
      if (el.requestFullscreen && document.fullscreenEnabled) return void await el.requestFullscreen();
    } catch {
      // Blocked (an embedded frame, a policy): cover the page instead.
    }
    setCovering(true);
  };
  return { ref, on, toggle };
}

/** The one button that opens and closes a view's full screen (useFullscreen). On the full screen it becomes a plain
 * close (X): the views carry no other way out on a phone, so it must read as the exit and stay clear of the notch. */
export function FullscreenButton({ on, onClick, className = "" }: { on: boolean; onClick: () => void; className?: string }) {
  const label = on ? "Close full screen" : "Full screen";
  return (
    <Button variant="secondary" size="sm" className={`ui-fullscreen-button${on ? " ui-fullscreen-close" : ""} ${className}`.trim()}
      onClick={onClick} aria-label={label} title={on ? "Close full screen (Esc)" : label}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={on ? 2.2 : 1.8}
        strokeLinecap="round" aria-hidden="true">
        <path d={on ? "M6 6l12 12M18 6L6 18" : "M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5"} />
      </svg>
    </Button>
  );
}

export { DialogHost, confirmDialog, promptDialog } from "./dialog";
