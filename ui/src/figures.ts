/** Figures worked out from what the API returned, each defined once. A page never adds up, counts or dates a list
 * itself: it shows a field the API returned, or calls one of these. A figure two pages show belongs here or on the
 * server, so the two can't disagree. */
import type { Stats } from "./api";

/** Open questions about the register, across every kind. */
export const openReviews = (stats: Pick<Stats, "open_reviews"> | null | undefined): number =>
  Object.values(stats?.open_reviews ?? {}).reduce((a, b) => a + b, 0);

/** Entities in the register, across every kind. */
export const entityTotal = (stats: Pick<Stats, "entities">): number =>
  Object.values(stats.entities).reduce((a, b) => a + b, 0);
