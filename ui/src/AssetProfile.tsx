/** What an asset is, at the top of its page: make and model (linked to the model's page), serial and the unit installed
 * now, class, criticality, where it sits, and its manual and P&ID sheets one tap away. What the IOC confirms before
 * dispatching, and what a technician checks on arrival; every value is the register's, and a fact no source states
 * is left out. */
import type { ReactNode } from "react";
import { api } from "./api";
import { openDocument, documentHref } from "./DocumentViewer";
import { openDrawing } from "./DrawingFullscreen";
import { entityHref, useAsync } from "./lib";
import { Card, Facts, Text } from "./ui";

/** What the card reads, shared with the asset page so it can draw its overview at once (#1069). */
export const readProfile = (id: string) =>
  Promise.all([api.entityProfile(id).catch(() => null), api.entityDrawings(id).catch(() => null)]);

export function AssetProfile({ id, data }: { id: string; data?: Awaited<ReturnType<typeof readProfile>> }) {
  const own = useAsync(() => (data === undefined ? readProfile(id) : Promise.resolve(undefined)), [id, data === undefined]).data;
  const [p, sheets] = data ?? own ?? [null, null];
  if (!p || !("location" in p)) return null;
  const make = [p.manufacturer, p.model].filter(Boolean).join(" ");
  const rows: [string, ReactNode][] = [];
  if (make) rows.push(["Make and model", p.equipment_model ? <a href={entityHref(p.equipment_model.id)}>{make}</a> : make]);
  if (p.serial || p.installed) rows.push(["Serial", <>
    {p.serial}
    {p.installed && <Text muted> · <a href={entityHref(p.installed.id)}>unit {p.installed.label}</a>
      {p.installed.since && <> installed {p.installed.since}</>}</Text>}
  </>]);
  if (p.position) rows.push(["Installed at", <>
    <a href={entityHref(p.position.id)}>{p.position.label}</a>
    {p.position.since && <Text muted> since {p.position.since}</Text>}
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
    <Card as="section" className="asset-profile" title="About this asset">
      <Facts grid items={rows} />
    </Card>
  );
}
