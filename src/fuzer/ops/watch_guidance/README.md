# The watch's guidance

Everything the agentic watch (`fuzer.ops.watch`) tells the model lives in this folder, so it can be read, changed and
moved to another application on its own. The code around it only gathers the evidence, asks once, and checks the
answer.

## What's here

- `prompt.md`: the whole instruction. It is sent as a single user message, with no system prompt, once per process area
  per day. It is a Python `str.format` template, so a literal `{` or `}` is written `{{` or `}}`, and these four
  placeholders are filled in:
  - `{area}`: the area's name.
  - `{since}`, `{until}`: the window, as ISO times (a day, midnight to midnight, plant time).
  - `{evidence}`: the evidence below, as indented JSON.

To use it elsewhere, fill those four placeholders and undo the `{{ }}` escaping (`prompt.md.format(...)` does both).

## The evidence (input)

One JSON object, built by `watch.evidence(conn, area, since, until)`. Each item carries an `id`, and the model may
cite only those ids (or the point names given).

| key | what it holds |
|---|---|
| `area`, `since`, `until` | the area and window, as above |
| `unexplained_stretches` | stretches the area's own multivariate model of the week before can't explain: `start`, `end`, `peak_spe`, and the three `points` (`id`, `name`) contributing most |
| `drifts` | points that moved on their own over the 90 days before: `id`, `name`, `baseline_from` (the day its baseline was read, not when it began), `baseline`, `now`, `change` (a share of the baseline), `peers_change`, `peers`, `stepwise` (held flat between a few moves: set by hand), `choke_moved` (its equipment's choke or valve was moved over the months; only where measurement roles are known), and `logged` (the last operator log entries on its equipment) |
| `detections` | detections already made on the area's points in the window: `id` (`detection:N`), `point_id`, `point`, `detector`, `what`, `start`, `end`, and for a slow drift the drift detector raised, `drift` with its baseline, level now and peers |
| `open_exceptions` | open exceptions on the area's plant: `id` (`exception:N`), `type`, `title` |
| `alarms` | alarms that sounded: `id` (the point), `point`, `alarm`, `limit`, `peak`, `start`, `end` |
| `log` | operator log entries: `id`, `at`, `text` |
| `notifications` | maintenance notifications raised: `id`, `notification`, `created`, `text` |

Drifts are judged as the drift detector judges them where measurement roles are known: positions, speeds, the weather
and wellhead temperatures are left out, each point is set against its role on its class of equipment, stopped or
shut-in stretches are ignored, and a point coming back to normal is not a drift. A field declining or a season warming
moves the peers too, so it isn't shown. A drift is shown from 3% of its level beyond its peers' move, or 10% on a well, whose pressures wander a few percent each on their own. At most 15 of each kind are given.

## The answer (output)

Free text (the reasoning), ending with one fenced JSON block:

```json
{"proposals": [{"subject": "<point or equipment>", "start": "<ISO time>", "end": "<ISO time>",
  "why": "<one or two sentences>", "ruled_out": "<what was ruled out>", "evidence": ["<id>", "..."]}]}
```

An empty list is the expected answer on most days. The watch then drops a proposal a detection already covers in its
window, and marks one citing an id it wasn't shown as uncited. It never routes a proposal anywhere until the watch has
earned live mode.

## How the guidance is scored

`fuzer eval --watch` replays fixed windows from each site bundle (`evals/watch.yaml`) and checks each proposal against
the upsets planted there. The bar: at least half of what the watch proposes must match an upset no detector caught.
Live mode is earned only by a run on the metered provider.

To iterate without spending, score offline: the same prompts, answered by a person or a Claude session on a
subscription.

1. `fuzer eval --watch --offline DIR` writes each question to `DIR/prompts/<key>.txt` and treats unanswered ones as
   no proposals.
2. Answer each prompt in `DIR/answers/<key>.md`, giving the whole answer, JSON block included. Use a fresh session per
   prompt, with nothing but the prompt in it.
3. Run the same command again to score. A run with any question unanswered doesn't pass, and an offline run never
   earns live mode.

The key is a hash of the prompt with entity ids numbered in order, because ids are minted at random each time a
database is built; an answer is read back with its ids mapped onto the current run's.
