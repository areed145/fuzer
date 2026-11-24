/** The docs page that explains each screen, by the first part of its route. The account menu's one Help entry opens
 * it; a screen with no page of its own opens the docs home. Pages carry no help link of their own. */
const DOCS = "https://areed145.github.io/fuzer/";
const PAGE: Record<string, string> = {
  inbox: "workflow", work: "workflow", cases: "workflow", processes: "workflow", workflows: "workflow", handover: "workflow",
  exceptions: "workflow", teams: "workflow", routing: "work-routing",
  metrics: "metrics", reports: "reports", regulatory: "reports", studies: "analyses", downtime: "analyses",
  ontology: "data-model", e: "data-model", sources: "sources", settings: "settings", users: "identity", ask: "agents",
};

export const helpHref = (section: string): string => `${DOCS}${PAGE[section] ? `${PAGE[section]}/` : ""}`;
