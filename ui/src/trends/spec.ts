// A trend spec (analysis/trend_spec.py): the one config every chart over time draws. The server decides what a trend
// shows and words every series; the chart (TrendChart) only draws it.
export type { TimelineData, TimelineEvent, TimelineLane } from "../trendFigure";

import type { TrendLimit } from "../trendFigure";
export type { TrendLimit };

export interface TrendSeries {
  key: string;
  point: string;
  /** The point as a person reads it: no server prefix, no value suffix. */
  tag: string;
  label: string;
  description: string | null;
  /** The unit as printed (°F, Sm³/d); `uom` is the source's. */
  unit: string;
  uom: string | null;
  role: string | null;
  on: { id: string; label: string | null; relation: string } | null;
  /** The record the series opens. */
  link: string;
  /** `step`: a landed reading (a well test, a lab sample), drawn as steps with a marker per reading. */
  as: "line" | "step";
  /** Series in a panel with the same axis share a scale. */
  axis: string;
  values: [string, number][];
  min: number;
  max: number;
  mean: number;
  last: number;
  limits: TrendLimit[];
  methods?: (string | null)[];
  modelled?: boolean;
  show: boolean;
}

export interface TrendPanel { key: string; label: string; show: boolean; axis: "auto"; series: string[] }

/** Drawn on the chart itself: a shaded window (a case's, the lead into an incident) or a moment (a step, a trip). */
export interface TrendMark { kind: "window" | "time"; start?: string; end?: string; at?: string; label: string; tone?: "warn" | "danger" | "accent" }

export interface TrendSpec {
  version: number;
  subject: string;
  unit: string;
  unit_label: string | null;
  title: string;
  template: { key: string; title: string; source?: "platform" | "bundle" };
  provider: string;
  step_minutes: number;
  time: { start: string; end: string; centre: string };
  panels: TrendPanel[];
  series: TrendSeries[];
  /** The series the chart opens on; the rest are one tap away. */
  focus: string[] | null;
  events: { lane: string; label: string; show: boolean }[];
  marks: TrendMark[];
  missing: { role: string; role_label: string; relation: string }[];
  errors?: string[];
  error?: string;
}
