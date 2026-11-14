/** What an asset is, at the top of its page: make and model (linked to the model's page), serial and the unit installed
 * now, class, criticality, where it sits, and its manual and P&ID sheets one tap away. What the IOC confirms before
 * dispatching, and what a technician checks on arrival; every value is the register's, and a fact no source states
 * is left out. */
import type { ReactNode } from "react";
import { api } from "./api";
import { openDocument, documentHref } from "./DocumentViewer";
import { openDrawing } from "./DrawingFullscreen";
import { entityHref, useAsync } from "./lib";
import { Card } from "./ui";

export function AssetProfile({ id }: { id: string }) {
  const p = useAsync(() => api.entityProfile(id), [id]).data;
  const sheets = useAsync(() => api.entityDrawings(id).catch(() => null), [id]).data;
  if (!p || !("location" in p)) return null;
  const make = [p.manufacturer, p.model].filter(Boolean).join(" ");
  const rows: [string, ReactNode][] = [];
  if (make) rows.push(["Make and model", p.equipment_model ? <a href={entityHref(p.equipment_model.id)}>{make}</a> : make]);
  if (p.serial || p.installed) rows.push(["Serial", <>
    {p.serial}
    {p.installed && <span className="muted"> · <a href={entityHref(p.installed.id)}>unit {p.installed.label}</a>
      {p.installed.since && <> installed {p.installed.since}</>}</span>}
  </>]);
  if (p.position) rows.push(["Installed at", <>
    <a href={entityHref(p.position.id)}>{p.position.label}</a>
    {p.position.since && <span className="muted"> since {p.position.since}</span>}
  </>]);
  if (p.equipment_class) rows.push(["Class", <a href={entityHref(p.equipment_class.id)}>
    {p.equipment_class.description ?? p.equipment_class.label}</a>]);
  if (p.criticality) rows.push(["Criticality", p.criticality]);
  if (p.location.length) rows.push(["Location", <span className="profile-path">
    {p.location.map((l, i) => <span key={l.id}>{i > 0 && " › "}
      <a href={entityHref(l.id)} title={l.description ?? undefined}>{l.description ?? l.label}</a></span>)}
  </span>]);
  if (p.manuals.length) rows.push([p.manuals.length > 1 ? "Manuals" : "Manual", <span className="profile-links">
    {p.manuals.map((d) => <a key={d.id} href={documentHref(d.id)} onClick={(ev) => { ev.preventDefault(); openDocument(d.id); }}>
      {d.title ?? d.label}</a>)}
  </span>]);
  const drawings = sheets?.drawings ?? [];
  if (drawings.length) rows.push(["On P&ID", <span className="profile-links">
    {drawings.map((s) => <a key={s.id} href={entityHref(s.id)}
      onClick={(ev) => { ev.preventDefault(); openDrawing(s.id, sheets!.highlight); }}>{s.label ?? s.id}</a>)}
  </span>]);
  if (!rows.length) return null;
  return (
    <Card as="section" className="asset-profile">
      <h2>About this asset</h2>
      <dl className="profile-facts">
        {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    </Card>
  );
}
