import "../styles/ui-kit.css";
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, LabelHTMLAttributes, ReactNode, Ref, SelectHTMLAttributes, TableHTMLAttributes } from "react";
import { useEffect, useId, useRef, useState } from "react";

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
  useEffect(() => {
    if (!confirming && wasConfirming.current) opener.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);
  return confirming ? <div className="notice" role="group" aria-label={`Confirm ${label}`}>
    <p>{consequence}</p>
    <Button type="button" variant="primary" disabled={busy || disabled} onClick={() => { setConfirming(false); onConfirm(); }}>Confirm {label}</Button>{" "}
    <Button type="button" autoFocus disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
  </div> : <Button ref={opener} type="button" variant="primary" disabled={busy || disabled} onClick={() => setConfirming(true)}>{label}</Button>;
}

type Width = "xs" | "sm" | "md" | "lg" | "full";

const DOCS = "https://areed145.github.io/fuzer/";

/** The page title, an optional one-line subtitle and the page's actions. `help` names a docs page
 * (for example "workflow") and adds a Help link, which is where explanations of the page belong. */
export function PageHeader({ children, subtitle, actions, help, className = "" }: {
  children: ReactNode; subtitle?: ReactNode; actions?: ReactNode; help?: string; className?: string;
}) {
  const tools = (actions || help) && <div className="ui-page-actions">{actions}{help && <HelpLink doc={help} />}</div>;
  return <header className="ui-page-header"><div><h1 tabIndex={-1} className={className}>{children}</h1>{subtitle && <p className="muted">{subtitle}</p>}</div>{tools}</header>;
}

/** A quiet link to the docs page that explains this screen. Use it instead of paragraphs of guidance on the page. */
export function HelpLink({ doc, children = "Help" }: { doc: string; children?: ReactNode }) {
  return <a className="ui-help" href={`${DOCS}${doc.replace(/^\//, "")}/`} target="_blank" rel="noreferrer">{children}</a>;
}

/** A message the user needs to act on. Not for explanations or disclaimers. */
export function Notice({ tone = "info", className = "", ...props }: HTMLAttributes<HTMLDivElement> & { tone?: "info" | "warn" | "danger" }) {
  return <div {...props} className={`notice ui-notice-${tone} ${className}`.trim()} />;
}

/** Links that switch between views of one page (a segmented control). The current view carries aria-current. */
export function Tabs({ items, label, className = "" }: {
  items: { key: string; label: ReactNode; href: string; current?: boolean }[]; label: string; className?: string;
}) {
  return <nav className={`tabs ${className}`.trim()} aria-label={label}>
    {items.map((item) => <a key={item.key} href={item.href} className={item.current ? "on" : undefined}
      aria-current={item.current ? "page" : undefined}>{item.label}</a>)}
  </nav>;
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

export function Card({ as: Component = "div", title, actions, children, className = "", ...props }: Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  as?: "div" | "section" | "aside" | "figure"; title?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
}) {
  return <Component {...props} className={`ui-card card ${className}`.trim()}>{(title || actions) && <header className="ui-card-header">{title && <h2>{title}</h2>}{actions}</header>}{children}</Component>;
}
export const Section = Card;

export function Table({ children, className = "", variant = "default", density = "normal", clickable = variant === "results", headingCase = "capitalize", ...props }: TableHTMLAttributes<HTMLTableElement> & {
  className?: string; variant?: "default" | "results" | "keyValue" | "list" | "compare" | "settings" | "mapping";
  density?: "normal" | "compact"; clickable?: boolean; headingCase?: "capitalize" | "verbatim";
}) {
  return <div className="ui-table-wrap"><table {...props} className={`ui-table ui-table--${variant} ui-table-${density}${clickable ? " ui-table-clickable" : ""} ui-table-heading-${headingCase} ${className}`.trim()}>{children}</table></div>;
}

export function Field({ label, hint, width = "md", children }: { label: ReactNode; hint?: ReactNode; width?: Width; children: ReactNode }) {
  return <label className={`ui-field ui-width-${width}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
export function Input({ width, className = "", ...props }: InputHTMLAttributes<HTMLInputElement> & { width?: Width; ref?: Ref<HTMLInputElement> }) {
  return <input {...props} className={`ui-input ${width ? `ui-width-${width}` : ""} ${className}`.trim()} />;
}
export function Select({ width, className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement> & { width?: Width }) {
  return <select {...props} className={`ui-input ${width ? `ui-width-${width}` : ""} ${className}`.trim()} />;
}
export function Badge({ tone = "muted", className = "", ...props }: HTMLAttributes<HTMLSpanElement> & {
  tone?: "ok" | "warn" | "danger" | "muted";
}) {
  return <span {...props} className={`ui-badge ui-badge-${tone} badge ${className}`.trim()} />;
}
export function Stat({ label, value, detail }: { label: ReactNode; value: ReactNode; detail?: ReactNode }) {
  return <div className="ui-stat"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
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
