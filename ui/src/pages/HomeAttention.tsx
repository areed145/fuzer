/** Where attention is needed (Home's open work layer): the places with open work, how they are filtered, and one place
 * opened to its most urgent items. Read only: dispatch and every edit stay on All work and the case pages. */
import { queue } from "../wording";
import { useEffect, useRef, useState, type TouchEvent } from "react";
import { type AttentionBand, type AttentionItem, type AttentionPlace } from "../api";
import { Badge, Button, ButtonLink, Line, Text, Heading } from "../ui";
import { entityHref, href } from "../lib";
import { ATTENTION_TONE } from "../kinds";
import { workHref } from "./AllWork";

export type Filter = "all" | "high" | "overdue" | "unassigned";
export const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All open" }, { key: "high", label: "High" },
  { key: "overdue", label: "Overdue" }, { key: "unassigned", label: "Unassigned" },
];
// The attention scale's tones (kinds.ts), so the words match the hotspot colours on the map and the 3D view.
const BAND: Record<AttentionBand, { label: string; tone: "danger" | "warn" | "info" }> = {
  high: { label: "High", tone: ATTENTION_TONE.high }, medium: { label: "Medium", tone: ATTENTION_TONE.medium },
  low: { label: "Low", tone: ATTENTION_TONE.low },
};
export const keep = (p: AttentionPlace, f: Filter) =>
  f === "all" || (f === "high" ? p.counts.high > 0 : f === "overdue" ? p.overdue > 0 : p.unassigned > 0);
export const placeName = (p: AttentionPlace) => p.name ?? p.label;
/** All work's place filter for the open work no place on the map holds (#1074). */
export const UNPLACED = "unplaced";
const holder = (i: AttentionItem) => i.assignee ?? (i.role ? queue(i.role) : "Unassigned");

/** One place: its most urgent items and where to take them, a bottom sheet on a phone. It closes from its X, Escape, a
 * tap anywhere outside it (a drag of the map is not a tap) or, on a phone, a swipe down. */
export function PlaceDetail({ place, onClose }: { place: AttentionPlace; onClose: () => void }) {
  const sheet = useRef<HTMLDivElement>(null);
  const swipe = useRef<number | null>(null);
  const [drag, setDrag] = useState(0);
  useEffect(() => {
    let down: { x: number; y: number } | null = null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();  // the first Escape closes the place; a full-screen map stays until the next one
      onClose();
    };
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e: PointerEvent) => {
      const target = e.target as Element | null;
      const tap = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 8;
      down = null;
      // Another place's marker or row picks that place instead; anything else outside the sheet closes it.
      if (!tap || !target || sheet.current?.contains(target) || target.closest(".hotspot, .feed-item")) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerup", onUp);
    };
  }, [onClose]);
  useEffect(() => { sheet.current?.style.setProperty("--drag", `${drag}px`); }, [drag]);  // the sheet follows the finger
  const touch = {
    onTouchStart: (e: TouchEvent<HTMLDivElement>) => { swipe.current = e.touches[0].clientY; },
    onTouchMove: (e: TouchEvent<HTMLDivElement>) => { if (swipe.current !== null) setDrag(Math.max(0, e.touches[0].clientY - swipe.current)); },
    onTouchEnd: () => { if (drag > 70) onClose(); swipe.current = null; setDrag(0); },
  };
  return (
    <div ref={sheet} className={`attention-detail${drag ? " dragging" : ""}`} role="region" aria-label={placeName(place)}>
      <div className="attention-detail-head" {...touch}>
        <span className="attention-grab" aria-hidden="true" />
        <div className="attention-detail-title">
          <Heading level={3}>{placeName(place)}</Heading>
          <Text small muted>
            {place.open} open · {place.overdue} overdue · {place.unassigned} unassigned · <Text mono>{place.label}</Text>
          </Text>
        </div>
        <Button variant="secondary" className="attention-close" onClick={onClose} aria-label="Close" title="Close">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </Button>
      </div>
      <ul className="attention-items">
        {place.top.map((i) => (
          <li key={i.id}>
            <Badge tone={BAND[i.band].tone}>{BAND[i.band].label}</Badge>
            <div>
              <a href={i.item_id ? href(`/cases/${i.item_id}`) : workHref("open", { q: i.title })}>{i.title}</a>
              {i.overdue && <> <Badge tone="danger">overdue</Badge></>}
              <Text as="div" small muted>{i.label} · {i.crew ? `${i.crew.who} ${i.crew.status}` : holder(i)}</Text>
            </div>
          </li>
        ))}
      </ul>
      <Line wrap>
        <ButtonLink variant="primary" href={workHref("open", { at: place.places.join(",") })}>Open {place.open} in All work</ButtonLink>
        <a href={entityHref(place.id)}>Asset page</a>
      </Line>
    </div>
  );
}
