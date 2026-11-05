import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TableHTMLAttributes } from "react";

type Width = "xs" | "sm" | "md" | "lg" | "full";

export function PageHeader({ children, subtitle, actions, className = "" }: { children: ReactNode; subtitle?: ReactNode; actions?: ReactNode; className?: string }) {
  return <header className="ui-page-header"><div><h1 className={className}>{children}</h1>{subtitle && <p className="muted">{subtitle}</p>}</div>{actions && <div className="ui-page-actions">{actions}</div>}</header>;
}

export function Button({ variant = "secondary", size = "md", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "link" | "danger"; size?: "sm" | "md";
}) {
  return <button {...props} className={`ui-button ${`ui-button-${variant}`} ui-button-${size} ${className}`.trim()} />;
}

export function Card({ title, actions, children, className = "", ...props }: Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  title?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
}) {
  return <div {...props} className={`ui-card card ${className}`.trim()}>{(title || actions) && <header className="ui-card-header">{title && <h2>{title}</h2>}{actions}</header>}{children}</div>;
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
export function Input({ width = "md", className = "", ...props }: InputHTMLAttributes<HTMLInputElement> & { width?: Width }) {
  return <input {...props} className={`ui-input ui-width-${width} ${className}`.trim()} />;
}
export function Select({ width = "md", className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement> & { width?: Width }) {
  return <select {...props} className={`ui-input ui-width-${width} ${className}`.trim()} />;
}
export function Badge({ tone = "muted", children }: { tone?: "ok" | "warn" | "danger" | "muted"; children: ReactNode }) {
  return <span className={`ui-badge ui-badge-${tone}`}>{children}</span>;
}
export function Stat({ label, value, detail }: { label: ReactNode; value: ReactNode; detail?: ReactNode }) {
  return <div className="ui-stat"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
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
