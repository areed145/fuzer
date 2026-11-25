import { request } from "./transport";
import type { AssistantFact, Lesson } from "./investigate";

/** Whether an automated feature may call a model now, and if not, why (no key, or the month's budget spent). */
export interface AiAvailability { available: boolean; why: string | null }

/** One eval question with its answer key: the facts a good answer names (a list entry is met by any of its terms) and
 * the specialists expected to answer it. */
export interface EvalQuestion { question: string; must_mention?: (string | string[])[]; specialists?: string[]; charts?: string[] }

/** A skill on the skills page (agents/skill_book.listing). */
export interface SkillRow {
  skill: string; name: string; title: string; area: string; area_label: string; owner: string; owner_label: string;
  agents: string[]; shared: boolean; purpose: string;
  live: { version: number; by: string | null; at: string | null };
  draft: { version: number; by: string; at: string; note: string | null; review_case: number | null } | null;
  shortfalls: number; taken: RecommendationsTaken | null; questions: number; score: number | null;
}

/** How often a skill's recommendations were taken since its live version (agents/shortfalls.taken): briefs followed
 * in full and recommended options chosen on a case. */
export interface RecommendationsTaken { decided: number; accepted: number; rate: number }

/** Where a skill fell short since its live version (agents/shortfalls): a brief not followed, an answer rated not
 * useful, or another option chosen than the one recommended. */
export interface SkillShortfall {
  kind: "brief" | "answer" | "option"; at: string; by: string; note: string | null;
  case?: number; title?: string; type?: string; followed?: string; proposed?: string; chosen?: string;
  answer?: string; question?: string;
}

export interface SkillDetail {
  skill: string; name: string; title: string; area: string; area_label: string; owner: string; owner_label: string;
  /** the agents that load it, by name, the owner first */ agents: string[]; personas: string[];
  shared: boolean; purpose: string; data: string; output: string; boundary: string;
  live: { version: number; by: string | null; at: string | null };
  playbook: string; body: string;
  pack: { classes: string[]; tools: string[]; methods: string[]; detectors: string[]; briefs: string[] } | null;
  tools: string[]; named_tools: string[]; questions: EvalQuestion[]; shortfalls: SkillShortfall[];
  taken: RecommendationsTaken | null; ai: AiAvailability;
}

/** The scores the eval gate kept on a version it published (agents/skill_gate.evaluate). */
export interface GateScores { score: number; mentions: number; routed: number; cost_usd: number; missing: { question: string; missing: (string | string[])[] }[] }
export interface GateResult {
  skill: string; version: number; live_version: number; questions: number; allowed: boolean; note?: string;
  live?: GateScores; candidate?: GateScores;
}

export interface SkillVersion {
  version: number; status: "draft" | "published" | "retired" | "rolled_back" | "refused";
  playbook: string; cases: Record<string, unknown>[]; questions: EvalQuestion[]; note: string | null;
  created_by?: string; created_at?: string; published_by?: string | null; published_at?: string | null;
  eval?: GateResult | Record<string, never>;
}

export interface SkillDiff {
  skill: string; version: number; against: number; added: number; removed: number;
  /** the questions the eval gate would ask to publish this version */ questions: number;
  lines: { op: "same" | "add" | "remove"; text: string }[];
}

/** A proposed fact on Knowledge, with the wording of the facts it would end. */
export interface ProposedFact extends AssistantFact { replaced: { id: number; text: string }[] }

/** A decision taken on Knowledge or a skill's page, newest first. */
export interface KnowledgeDecision {
  kind: "fact" | "lesson" | "skill"; ref: string; title: string; status: string; by: string | null; at: string | null; note: string | null;
}

export interface KnowledgeQueue {
  facts: ProposedFact[]; lessons: Lesson[]; skills: SkillRow[]; recent: KnowledgeDecision[];
  counts: { facts: number; lessons: number; skills: number; total: number };
}

/** One check result on a kept output (agents/output_checks), or one answer of a skill's eval (agents/skill_gate). */
export interface EvalResult {
  // a skill's answer
  question?: string; mentions?: number | null; missing?: (string | string[])[]; routed?: boolean; specialists?: string[];
  must_mention?: (string | string[])[]; answer?: string; cost_usd?: number;
  // a kept output
  ref?: string; label?: string; href?: string; at?: string; text?: string;
  checks?: { ok: boolean; uncited_points?: string[]; uncited_lines?: string[]; unknown_citations?: string[];
    unknown_numbers?: string[]; operating_instructions?: string[]; long_headline?: boolean };
  grade: { score: number | null; why: string } | null;
}

export interface EvalRun {
  id: number | string; origin: "run" | "recorded"; subject: string; status: "running" | "done" | "failed";
  by: string | null; at: string; finished_at: string | null; scores: Record<string, number | null>;
  /** checks or facts named, 0 to 1 */ score: number | null;
  grade_status: "waiting" | "grading" | "graded" | "failed" | "not_graded"; /** the model's grade, 0 to 1 */ grade: number | null;
  grade_why: string | null; error: string | null; cost_usd: number;
  results?: EvalResult[]; label?: string; grade_model?: string | null;
}

export interface EvalSubject {
  subject: string; label: string; kind: "skill" | "outputs"; group: string; questions: number | null;
  latest: EvalRun | null; trend: number[];
}

/** One question a skill's eval asks (agents/eval_questions). `key` is null for a question drawn from a case. */
export interface KeptQuestion {
  key: string | null; origin: "shipped" | "added" | "case"; shipped: string | null; question: string;
  must_mention: (string | string[])[]; specialists: string[]; charts: string[]; status: "active" | "retired";
  edited: boolean; by: string | null; at: string | null; note: string | null; version?: number;
}

export interface EvalSubjectDetail {
  subject: string; label: string; kind: "skill" | "outputs"; runs: EvalRun[]; questions: KeptQuestion[] | null; ai: AiAvailability;
}

export const agentsApi = {
  evals: () => request<{ subjects: EvalSubject[]; ai: AiAvailability }>("GET", "/evals"),
  evalSubject: (subject: string) => request<EvalSubjectDetail>("GET", "/evals/subject", { subject }),
  evalRun: (id: number | string) => request<EvalRun>("GET", `/evals/runs/${encodeURIComponent(String(id))}`),
  runSkillEval: (skill: string) => request<EvalRun>("POST", "/evals/skills/run", undefined, { skill, by: "" }),
  runOutputsEval: (subject: string) => request<EvalRun>("POST", "/evals/outputs/run", undefined, { subject, by: "" }),
  gradeEvalRun: (id: number) => request<EvalRun>("POST", `/evals/runs/${id}/grade`),
  saveEvalQuestion: (q: { skill: string; key: string | null; question: string; must_mention: (string | string[])[]; specialists: string[] }) =>
    request<KeptQuestion>("PUT", "/evals/questions", undefined, { ...q, by: "" }),
  retireEvalQuestion: (skill: string, key: string) => request<KeptQuestion>("POST", "/evals/questions/retire", undefined, { skill, key, by: "" }),
  restoreEvalQuestion: (skill: string, key: string) => request<KeptQuestion>("POST", "/evals/questions/restore", undefined, { skill, key, by: "" }),
  knowledgeReview: () => request<KnowledgeQueue>("GET", "/knowledge/review"),
  approveFact: (id: number, by: string) => request<AssistantFact & { ended: number[] }>("POST", `/assistant/facts/${id}/approve`, undefined, { by }),
  rejectFact: (id: number, by: string, note: string) => request<AssistantFact>("POST", `/assistant/facts/${id}/reject`, undefined, { by, note }),
  skills: () => request<{ skills: SkillRow[]; ai: AiAvailability }>("GET", "/work/skills"),
  skill: (skill: string) => request<SkillDetail>("GET", "/work/skills/detail", { skill }),
  skillVersions: (skill: string) => request<SkillVersion[]>("GET", "/work/skills/versions", { skill }),
  skillDiff: (skill: string, version: number) => request<SkillDiff>("GET", "/work/skills/diff", { skill, version }),
  /** `replaces`: the draft this one was written from, set aside by the server so one draft waits. */
  draftSkill: (skill: string, playbook: string, by: string, note?: string, replaces?: number) =>
    request<{ skill: string; version: number; status: string }>("POST", "/work/skills/versions", undefined,
      { skill, playbook, by, note: note || null, replaces: replaces ?? null }),
  publishSkill: (skill: string, version: number, by: string) =>
    request<{ version: number; evaluation: GateResult }>("POST", "/work/skills/publish", undefined, { skill, version, by }),
  setSkillAside: (skill: string, version: number, by: string, note?: string) =>
    request<{ version: number; status: string }>("POST", "/work/skills/set-aside", undefined, { skill, version, by, note: note || null }),
  rollBackSkill: (skill: string, by: string) =>
    request<{ version: number; rolled_back: number }>("POST", "/work/skills/rollback", undefined, { skill, by }),
};
