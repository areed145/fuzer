// Changing a report definition in the editor (docs/reports.md, "Custom reports"): move a panel up or down, size it,
// remove it, add a row with a new panel. Each returns a new definition; the server checks it when it is saved.
import type { Panel, PanelColumn, PanelSize, ReportDefinition, ReportRow } from "./spec";

/** Where a panel (or a column of panels) sits: its row and its place in that row. */
export interface Place { row: number; item: number }
type Item = Panel | PanelColumn;

const copyRows = (d: ReportDefinition): ReportRow[] => d.rows.map((r) => ({ ...r, panels: [...r.panels] }));
const withRows = (d: ReportDefinition, rows: ReportRow[]): ReportDefinition => ({ ...d, rows: rows.filter((r) => r.panels.length) });

function rowId(rows: ReportRow[]): string {
  const used = new Set(rows.map((r) => r.id));
  let n = rows.length;
  while (used.has(`r${n}`)) n += 1;
  return `r${n}`;
}

/** Every panel id in the definition, out of its columns. */
export function panelIds(d: ReportDefinition): Set<string> {
  return new Set(d.rows.flatMap((r) => r.panels.flatMap((p) => (p.type === "column" ? p.panels.map((q) => q.id) : [p.id]))));
}

/** A new panel id, from what it shows ("work.backlog" gives "backlog"), unique in the report. */
export function newPanelId(d: ReportDefinition, base: string): string {
  const used = panelIds(d);
  const stem = (base.split(".").pop() || "panel").replace(/[^a-z0-9_-]+/gi, "-").toLowerCase() || "panel";
  if (!used.has(stem)) return stem;
  let n = 2;
  while (used.has(`${stem}-${n}`)) n += 1;
  return `${stem}-${n}`;
}

/** Move one place earlier (-1) or later (+1) in reading order: within its row, then into the row before or after.
 * At either end of the report a panel that shares its row gets a row of its own. */
export function move(d: ReportDefinition, at: Place, step: -1 | 1): ReportDefinition {
  const rows = copyRows(d);
  const row = rows[at.row];
  const next = at.item + step;
  if (next >= 0 && next < row.panels.length) {
    [row.panels[at.item], row.panels[next]] = [row.panels[next], row.panels[at.item]];
    return withRows(d, rows);
  }
  const [item] = row.panels.splice(at.item, 1);
  const to = at.row + step;
  if (to >= 0 && to < rows.length) {
    if (step < 0) rows[to].panels.push(item); else rows[to].panels.unshift(item);
  } else if (row.panels.length) {
    rows.splice(step < 0 ? 0 : rows.length, 0, { id: rowId(rows), panels: [item] });
  } else {
    row.panels.push(item);  // alone at the very top or bottom: nowhere to go
  }
  return withRows(d, rows);
}

export function canMove(d: ReportDefinition, at: Place, step: -1 | 1): boolean {
  const alone = d.rows[at.row].panels.length === 1;
  return !(alone && (step < 0 ? at.row === 0 : at.row === d.rows.length - 1));
}

export function resize(d: ReportDefinition, at: Place, size: PanelSize): ReportDefinition {
  const rows = copyRows(d);
  rows[at.row].panels[at.item] = { ...rows[at.row].panels[at.item], size } as Item;
  return withRows(d, rows);
}

export function remove(d: ReportDefinition, at: Place): ReportDefinition {
  const rows = copyRows(d);
  rows[at.row].panels.splice(at.item, 1);
  return withRows(d, rows);
}

/** A new row holding `panel`, before row `index` (the report's length adds it at the end). */
export function addRow(d: ReportDefinition, index: number, panel: Panel): ReportDefinition {
  const rows = copyRows(d);
  rows.splice(index, 0, { id: rowId(rows), panels: [panel] });
  return withRows(d, rows);
}
