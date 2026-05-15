# Federated DAG Membership Anomalies

Two rooms exhibit membership divergence where remote servers disagree about who is joined. Both cases share a common pattern: **events at DAG boundaries are missing from remote peers' resolved state**, likely due to how continuwuity exposes extremities and subgraphs during federation backfill.

---

## Anomaly 1 — V12 `!DGMOlu` (matrix.org dropping `@gg` / `@shane:wombatx.me`)

**Room:** `!DGMOlu0bsNLoPM7HTY-CRWUq1a1w0wUX_F13mwkYHr0` (v12, 48 events)

**Symptom:** matrix.org does not see `@gg:nutra.tk` or `@shane:wombatx.me` as joined.

**DAG structure:**

- Linear chain from depth 1–31 (room creation, initial joins, state events)
- **Fork at depth 32–33:** Two parallel branches diverge from depth 31
  - Branch A: `@sys:31a05b.net` joins (d=32, d=33)
  - Branch B: `@gamesguru:catgirl.cloud` joins (d=32, d=33)
- **Merge at depth 35:** `@gg:nutra.tk` sends a message with 2 `prev_events`, merging both branches
- **Fork at depth 45:** `@shane:wombatx.me` and `@dwam:matrix.pyrosec.is` both join referencing the same parent

**Root cause hypothesis:**
The room's `m.room.power_levels` at depth 3 has `"users": {}` — **no explicit power entries**. The creator `@gg:nutra.tk` relies entirely on implicit creator power. During V12 state resolution of the depth 32–33 fork, matrix.org's iterative auth check must reconstruct `@gg`'s authority from the auth chain. If the auth state supplied during the check doesn't include the `m.room.create` event establishing `@gg` as creator, the power_levels event appears to grant nobody power, and downstream events sent by `@gg` (including the merge at d=35 and all subsequent state) fail authentication.

This cascades: if `@gg`'s merge event is rejected, the fork is never resolved from matrix.org's perspective. Later joins by `@shane:wombatx.me` (d=45) inherit from a branch matrix.org considers invalid.

**Continuwuity link:** When continuwuity serves `get_missing_events` or backfill responses, the subgraph it returns may not include sufficient auth context for the fork merge. If the response references old extremities from Branch B but omits the auth events needed to validate `@gg`'s implicit power, the receiving server (matrix.org) resolves the fork differently.

---

## Anomaly 2 — V11 `!tgmfqA` (catgirl.cloud missing `@bot:nutra.tk`)

**Room:** `!tgmfqAWaBc978M80V9:nutra.tk` (v11, 848 events from matrix.org)

**Symptom:** catgirl.cloud does not see `@bot:nutra.tk` as joined.

**DAG structure:**

- `@bot:nutra.tk` originally joined at depth 24 (Feb 3)
- catgirl.cloud was **already missing the bot from its member list** well before any remediation
- The rapid join/leave/join/leave/join cycle at depths 817–820 (May 6, all within 6 minutes) was a **deliberate attempt to force catgirl.cloud to re-acknowledge the bot** — it didn't work
- catgirl.cloud's `get-remote-dag` crawl returns only 11 events (depth 821–831), but it likely has most of the room's messages locally — just not bot membership events

**Key observation:** The gap is **selective**. catgirl.cloud received and processed most of the room's messages throughout its history, but `@bot:nutra.tk`'s membership events were never absorbed into its resolved state — not even after explicit remediation attempts.

**Root cause hypothesis:**
The bot's join at d=24 was likely **correctly resolved into catgirl.cloud's state initially**. At some later point, a state reset or re-evaluation event caused catgirl.cloud to recalculate its resolved state, and the bot's membership was **dropped during that recalculation**. Once removed, the bot could not be re-added — subsequent membership events (including the deliberate remediation cycle at d=817–820) would fail auth checks against the now-incorrect resolved state.

Possible triggers for the state loss:

1. **Fork resolution re-evaluation:** The room has multiple fork/merge points (21 merge events with >1 prev_events). When catgirl.cloud processes a new merge event, it re-runs state resolution across the conflicting branches. If the bot's membership event lands on a branch that loses during iterative auth checks (e.g., due to missing auth context for `@bot:nutra.tk`'s invite/join chain), the bot gets dropped from resolved state.

2. **Extremity/subgraph reset:** If continuwuity's extremity tracking resets or recalculates (e.g., after a server restart, database compaction, or DAG healing operation), the resolved state may be recomputed from a subset of events that doesn't include the bot's join — particularly if the bot's membership sits on an old branch that the new extremity set doesn't reference.

3. **Auth chain gap during re-evaluation:** The bot's join at d=24 requires specific auth events (create, power_levels, join_rules). If any of these auth events are missing or unreachable during a state re-evaluation, the bot's join fails auth and is silently excluded from the new resolved state.

**Continuwuity link:** The `get-remote-dag` crawl returning only 11 events from catgirl.cloud is telling — catgirl.cloud's extremities point to depth 821+, meaning it never incorporated the bot's events into its DAG at all. This is consistent with continuwuity serving events that reference the bot's membership as `prev_events` without including the bot's membership event itself in subgraph responses, preventing catgirl.cloud from ever filling the gap.

---

## Common Thread

Both anomalies involve **state-carrying events that sit at subgraph boundaries**:

1. In Anomaly 1, the merge event at d=35 (which resolves a fork) depends on implicit creator power that remote servers can't verify without sufficient auth context.
2. In Anomaly 2, the bot's final join at d=820 sits exactly one depth below the backfill boundary, creating a gap in the receiving server's resolved state.

In both cases, the issue is amplified by **extremity-based subgraph serving**: continuwuity tracks extremities to determine what events to include in federation responses. If the extremity set is stale (referencing old branches) or the subgraph depth limit is too shallow, critical state events near the boundary are omitted, and remote servers resolve membership differently.

### Possible remediations

- **Auth event inclusion:** Ensure `get_missing_events` responses always include referenced auth events, not just the DAG path
- **Extremity hygiene:** Prune stale extremities that reference long-dormant branches so backfill responses start from the correct frontier
- **Boundary padding:** When serving subgraphs, include at least N additional events beyond the requested range to prevent off-by-one gaps at the boundary
