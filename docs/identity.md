# Identity and golden records

How records that describe the same thing become one entity.

## Tag grammar does most of the matching
Each asset pack defines how its tags are written. One parser reads all the
styles we see and turns them into one canonical key:

| Source | As written | Canonical key |
|---|---|---|
| SAP FLOC | `OSB-20-PT1001A` | `20-PT-1001A` |
| PI (new) | `OSB.20PT1001A.PV` | `20-PT-1001A` |
| PI (DCS) | `OSB:23-PT-1001A.DACA.PV` | `23-PT-1001A` |
| PI (legacy) | `27PT1001A_PV` | `27-PT-1001A` |
| JDE text | `OIL EXPORT PUMP - 27-P1001-A` | `27-P-1001A` |
| Maximo | `OSB/20/P-1001A` | `20-P-1001A` |
| P&ID bubble | `PT` / `1001A` on two lines | `27-PT-1001A` (area from sheet title) |

Wells get the same treatment (`A07`, `A-7`, `OSB_A-07` → `A-07`;
`A07A`, `A-07 ST1` → `A-07 ST1`).

## Matching = exact keys first, then scoring
1. Records with the same kind and canonical key (tag key, well key, normalized
   serial number) merge automatically.
2. Equipment without a shared serial is compared with others **at the same
   tag** using manufacturer and description similarity.
   - score ≥ 0.90 → merge
   - 0.60–0.90 → review queue
   - below → leave apart
3. Two items at the same tag with different serials can never merge.
4. If one record could match two different things, it goes to review.
5. A reviewer's answer covers the whole item: rejecting a match keeps the record
   apart from every record merged with the other side, so the queue doesn't ask
   the same question again about another system's copy.

The scorer is small on purpose. When we need more power we swap in
[Splink](https://github.com/moj-analytical-services/splink) behind the same
interface.

## Stable IDs
Fuzer gives every entity its own ID (`fz-tag-…`, `fz-equipment-…`) and keeps a
**crosswalk** from every source record to it. On rerun, each cluster keeps the
ID most of its members already had. When two clusters merge, the losing ID
points to the winner (`merged_into`). IDs are never reused. Every mint, merge
and split is logged in `id_event`.

## Assertions → golden records
Every field from every source is an **assertion** with where it came from (re-read
from the entity's records when needed; only a reviewer's choice is stored). The
**golden record** is computed from assertions using per-field rules, seeded from
the asset pack and kept in the source catalog (`fuzer sources rules`; the default
priority is the systems' rank):

```yaml
manufacturer: {priority: [review, sap, maximo, jde], review_on_conflict: true}
uom:          {priority: [review, pi, pi_af]}
```

If sources really disagree on a `review_on_conflict` field, a review item is
raised. A reviewer's choice is stored as an assertion with source `review`,
which always wins and survives reruns.
