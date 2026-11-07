import { request } from "./transport";

export type HealthLevel = "green" | "amber" | "red" | "unknown";

export interface JobsHealth {
  level: HealthLevel;
  available: boolean;
  failed_runs_24h: number;
  failing_checks: number;
  scheduler_running: boolean;
  summary: string;
}

export interface JobTick {
  status: string;
  time: string | null;
  skip_reason: string | null;
  run_keys: string[];
  error: string | null;
}

export interface JobTrigger {
  kind: "schedule" | "sensor";
  name: string;
  status: "on" | "off";
  cron?: string;
  timezone?: string | null;
  next_tick?: string | null;
  description?: string | null;
  job?: string | null;
  last_tick: JobTick | null;
}

export interface JobRunSummary {
  run_id: string;
  job: string;
  status: string;
  start: string | null;
  end: string | null;
  seconds: number | null;
  trigger: string;
}

export interface JobCheck {
  name: string;
  asset: string;
  description: string | null;
  level: "pass" | "warn" | "fail" | "running" | "not_run";
  metadata: Record<string, unknown>;
  note: string | null;
  time: string | null;
  run_id: string | null;
}

export interface JobsOverview {
  available: boolean;
  dagster: { url: string; reachable: boolean };
  scheduler: { running: boolean; last_heartbeat: string | null };
  health: JobsHealth;
  jobs: { name: string; description: string | null; triggers: JobTrigger[]; last_run: JobRunSummary | null; recent_runs: JobRunSummary[] }[];
  sensors: JobTrigger[];
  checks: JobCheck[];
  claude: {
    schedule: "on" | "off" | "none";
    usd_30d: number;
    limits: Record<string, number | boolean>;
    runs: (JobRunSummary & { model: string; input_tokens: number; output_tokens: number; usd: number })[];
  } | null;
}

export interface JobRunDetail extends JobRunSummary {
  tags: Record<string, string>;
  assets: { asset: string; time: string | null; metadata: Record<string, unknown> }[];
  errors: { step: string | null; kind: string; message: string; error_class: string | null; stack: string | null }[];
  log_tail: { time: string | null; level: string; step: string | null; message: string }[];
}

export const jobsApi = {
  jobs: () => request<JobsOverview>("GET", "/jobs"),
  jobsHealth: () => request<JobsHealth>("GET", "/jobs/health"),
  jobRun: (id: string) => request<JobRunDetail>("GET", `/jobs/runs/${encodeURIComponent(id)}`),
};
