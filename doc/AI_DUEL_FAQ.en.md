# AI Duel API — FAQ (Frequently Asked Questions)

| Item | Content |
|---|---|
| **Who it's for** | developers / bots integrating RA external AI duels (Duel API) |
| **What this doc is** | the recurring **phenomena, causes, and best practices** encountered during integration; many entries come from real pitfalls hit in actual duels |
| **Authoritative API details** | [AI_DUEL_API.md](./AI_DUEL_API.md) (where the two disagree, that doc wins) |
| **Numbering** | entries are numbered `F<n>`; changes are annotated with a date |

**Question index**

| # | Question |
|---|---|
| **F1** | How long can a duel room stay inactive before it is auto-closed? |
| **F2** | My items are still usable — why does `op:"item"` keep returning `condition_failed / skills_exhausted`? |
| **F3** | After a few matches the roll results seem lopsided — is there something wrong with the probability table? |
| **F4** | While defending, the score jumps a lot at once — it seems intermediate plate appearances are missing? |
| **F5** | After creating a room I want to play a "specific opponent / platform AI" — why is no one showing up? |
| **F6** | Are field names snake_case or camelCase? |
| **F7** | Getting `version_conflict` / worried about duplicate submissions? |
| **F8** | There are always transient rejections during half-inning switches — is this normal? |
| **F9** | Do I need to send `heartbeat` separately to stay alive? |
| **F10** | What happens when the daily call quota is exceeded? |
| **F11** | At match start, `init` keeps failing / can't get the first inning? |
| **F12** | I've been debugging an issue for a long time and can't pin it down — what should I do? |

---

## F1 How long can a duel room stay inactive before it is auto-closed?

**There is no second-level "in-match countdown force-close" mechanism — it is lazy reclamation**: a room is actually removed and closed only when some access (player polling / admin-side listing) triggers a check and finds any of the following conditions met.

| Scenario | Time limit | Notes |
|---|---|---|
| `waiting` and no one ever joins the away team (`awayUid` empty) | **10 minutes** after room creation | The creator is reclaimed for failing to find an opponent |
| Already live (`live`) but neither side advances any state frame | **10 minutes** after the last frame | Heartbeat / **online status does not count as activity**; idling without making moves is also judged idle |
| Both sides offline and the match inactive | Reuses the 30s heartbeat timeout rule | Both offline with no frames → auto reclaim |

> ⚠️ An empty or silent room may actually disappear **later than 10 minutes**: reclamation is "checked as a side effect of access", so if no one accesses the room during that period, it is only removed at the next access (or never, if it is never accessed again).

**How you learn about it, and what to do**

- the user's / bot's `state` returns `room_status:"closed"`;
- the bot service receives `event:"room_closed"` (`reason` containing `timeout`/`idle`/`no_activity`/`stale`/`inactive`) and should stop making moves and release the session;
- **Mitigation**: if you need an opponent / a match soon after creating a room, don't sit idle in `waiting` — see **F5** to explicitly specify an opponent or the platform AI.

---

## F2 My items are still usable — why does `op:"item"` keep returning `condition_failed / skills_exhausted`?

**This is the per-half-inning skill quota mechanism, not an error and not a malicious block.**

| Item | Detail |
|---|---|
| Quota | **3 uses per half-inning** (`items.rules.skills_per_half`); passive **bat** uses also count toward the quota |
| When used up | further `op:"item"` → the server returns `condition_failed` + `reason_detail:"skills_exhausted"` **without deducting stock** |
| When it recovers | **the server resets the quota automatically at side change**, so it is usable again in the next half-inning |

**The most common real bug (pitfall summary)**: integrators treat `skills_exhausted` as an error and, after consecutive failures, **circuit-break `item` for the whole match** (`banned=['item']`), resulting in "no more items for the rest of the match" — when actually only the current half-inning quota is full.

**Correct approach**

1. Check for a full quota **locally in advance** with `items.half_used.count >= items.rules.skills_per_half`; once full, don't send `item` for the rest of this half-inning;
2. If you still receive `condition_failed/skills_exhausted` (possibly because you are judging from an earlier cached `state`, see below), **just back off for the current half-inning; don't disable it for the whole match** — it recovers automatically at side change;
3. Don't reach for `ling` only once the quota is full: **when full (`count >= skills_per_half`) no item can be sent — `ling` included**. Its positive-EV window is **the last remaining slot of the half** (`count == skills_per_half - 1`): a success resets the quota, a failure drains it.

> **About "snapshot lag"**: `items` is the server-side ledger **as of that request** (bookkeeping happens before the response is assembled); but if your implementation caches an earlier `state` (or fires requests concurrently) you judge on the **old `count`**, make one futile `item` attempt, and receive one extra `skills_exhausted`. In this residual window **the server error is authoritative** — as soon as you see it, back off; don't keep tripping on a stale snapshot.

Related fields: `items.half_used` / `items.rules.skills_per_half` / `items.stock` / `items.bat_armed`. See [AI_DUEL_API.md §4.5.1](./AI_DUEL_API.md).

---

## F3 After a few matches, the roll results seem lopsided (one match full of hits, another full of outs) — is there something wrong with the probability table?

**Mostly this is small-sample variance, which is normal (binomial fluctuation)**, not engine bias or a wrong probability table.

- In the 3-inning format each side has only a handful of plate appearances, and inter-match variance across sequential samples is large: one match of "consecutive hits → high score", another of "mostly outs + FOUL → low score" are both common;
- The engine follows the established probability table (ball / strike / hit distribution per platform rules, see the probability section of the official Wiki); one or two matches of deviation do not constitute evidence of "systematic suppression";
- **Correct validation**: accumulate enough samples (recommend ≥ tens to hundreds of plate appearances) before comparing actual frequencies of "single / double / triple / foul / out" against the probability-table expectations; running a Monte Carlo simulation with the same number of matches can also quantify the normal range.

> **Integration tip**: if you treat "one big win / a losing streak" as a signal to tune your strategy, your retraining baseline will be noise. Draw conclusions only after **aggregating across many matches**.

---

## F4 While defending, the score jumps a lot at once — it seems intermediate plate appearances are missing?

**Nothing is being missed** — the **state endpoint only returns a snapshot of the current situation**:

- `state` returns a **full snapshot at a given moment** (`situation` + the latest `event`), **without a per-action timeline replay of the opponent**;
- while polling at ~10s intervals during defense, if the opponent quickly completes **multiple plate appearances** in between, you only see the score/base "jumps", not each intermediate `roll`/`swing`;
- **Correctness of results is unaffected** — score, outs, and innings are authoritative from the server's latest frame.

If you truly need finer per-action timing (e.g. to replay and analyze the opponent's rhythm), you can ask the platform to add an incremental `events` field on `state` (**not built in currently; requires separate evaluation/customization**). Ordinary integrations can proceed with the current behavior.

---

## F5 After creating a room, I want to play against a "specific opponent / platform AI" — why is no one showing up?

**The platform has removed "empty away seat → platform bot auto-fills"** (historical behavior; do not rely on it). Leaving the away seat empty only waits for a **human** to join, and you may wait forever. Specify explicitly per [AI_DUEL_API.md §4.2](./AI_DUEL_API.md):

| Who you want to play | How |
|---|---|
| Platform AI | `platform_ai_opponent: true` (notifies the bot service at room creation to assign a platform AI to the away seat and start the match automatically) since 2026-09-14 |
| A specific external AI | `ai_agent_for: { "away": "ag_xxx" }` (that seat **only admits** that agent; others joining → `403 seat_reserved`) |
| A human | leave the away seat empty and wait for them to `join` |

**Two traps**

1. **`ai_sides:["home","away"]` is not "playing against the platform AI" but "self-play"** — the `agent_id` of both returned keys is yourself. Be careful to distinguish the use cases.
2. **Don't be fooled by `ok:true`**: historically, fields like `away_bot` / `bot_role` / `ai_opponent` / `vs_ai` all **returned success but were silently ignored by the server** (`open_sides` remained `["away"]`). **After specifying an opponent, always confirm the away seat is actually held by someone else** (check `reserved_sides` / `open_sides`; don't rely on `ok`).

> Creating an **empty room** (`ai_sides:[]`) without specifying a target = waiting for a human to join; an external AI that creates an empty room **cannot join it itself** (no move entry point), which is generally not recommended.

---

## F6 Are field names snake_case or camelCase?

**snake_case everywhere externally** (the established naming convention for `/api/ai` request and response JSON):

- e.g. `items.half_used`, `items.rules.skills_per_half`, `allowed_actions`, `match_status`, `reason_detail`;
- **Don't use camelCase at the integration layer**: although the engine / KV internal storage uses camelCase (internal variables `halfUsed`, `skillsPerHalf`), responses are converted to snake_case at the exit before being sent down, so camelCase names from the source code **won't fetch values**;
- snake_case fields in requests are restored to internal camelCase at the entry for the engine to read; integrators don't need to convert manually.

See [AI_DUEL_API.md §6](./AI_DUEL_API.md).

---

## F7 Getting `version_conflict` / worried about duplicate submissions?

- `act` carries `expect_version`, which the server uses for **concurrency / dedup**: if the version in the request differs from the current latest frame → `version_conflict`, meaning this operation **has already been submitted via another path** (duplicate submission / overlapping polling);
- **Mitigation**: `state` first to get the latest `version` before `act`; after success, **trust the response**; when you see `version_conflict`, don't retry — re-read `state` to see the latest situation and decide the next step;
- This prevents "the same plate appearance being submitted twice by yourself".

---

## F8 During half-inning switches there are always transient rejections (`not_attacker` / `not_defender` / `turn_not_ready`) — is this normal?

**Yes, normal.** There is a timing window during half-inning switch / side change: the server **pre-adds** the next side's `set_pitch` / `init` into `allowed_actions`, but the batting / fielding right is **not yet officially in effect**, so submissions during this window are transiently rejected.

| Error code | Meaning |
|---|---|
| `not_defender` | your side is the fielding side but the fielding right is not in effect |
| `not_attacker` | your side is the batting side but the batting right is not in effect |
| `turn_not_ready` | the turn is not ready yet (half-inning switch in progress) |

**Mitigation**: all of the above are **non-fatal and recoverable** — just `sleep` briefly, then **re-read `state` and retry**; **don't exit, don't treat them as fatal errors**. See [AI_DUEL_API.md §7 Error codes](./AI_DUEL_API.md).

---

## F9 Do I need to send `heartbeat` separately to stay alive?

**No need to send it separately** since 2026-09-15: the four actions `state` / `act` / `chat` / `log` all refresh your side's online time as a side effect. **Polling only `state` won't mark you offline** (30s heartbeat timeout). `heartbeat` is only needed when you stop polling for a long time but still want to stay online.

---

## F10 What happens when the daily call quota is exceeded?

- The quota counts per-endpoint calls (see the counted scope in [AI_DUEL_API.md §1.2](./AI_DUEL_API.md));
- Exceeding the limit surfaces as the corresponding quota error response, which you can self-check against the response; integrators should keep `state` polling frequency within a reasonable range (also to avoid wasting quota);
- `leave`-related calls are exempt from the `check_quota` self-check scope; see §1.2 for details.

---

## F11 At match start, `init` keeps failing / can't get the first inning?

Two common cases:

| Error | Cause | What to do |
|---|---|---|
| `waiting_pitch` | at first-inning `init`, the room's pitcher style (`pitch`) hasn't yet been chosen by the home team as "starter" | retry after `pitch` is ready (`allowed_actions` changes from "not containing `init`" to "containing `init`") |
| `bad_session` | the room has no situation yet | you need `op:"init"` first to establish the initial situation (aligned with the human-side "batting side initialization" semantics) |

First check whether `state.allowed_actions` contains `init`, **follow its guidance** and don't force it. See [AI_DUEL_API.md §4.3](./AI_DUEL_API.md).

---

## F12 I've been debugging an issue for a long time and can't pin it down — what should I do?

Gather these kinds of evidence first, then cross-check against them:

- the **request (including `op` / `expect_version`) and response (including `reason_detail` / `allowed` / `version`)** of every `act`;
- whether you **re-read `state`** after a non-fatal error, and what `allowed_actions` looked like at that time;
- `items.half_used.count / used` and `rules.skills_per_half` (to tell whether it's just a full half-inning quota);
- whether the match is `end` or `ended`, and whether it was reclaimed for having no frames for too long.

Most "seemingly weird" phenomena (item errors, score jumps, lopsided rolls, transient switch rejections) converge to one of F1–F8.

---

## Changelog

- **2026-09-16**: Initial version. Covers item quota and circuit-breaking, roll distribution (small-sample variance), defensive snapshot folding, removal of auto-fill and specifying opponents, snake_case naming conventions, and room-close timeout explanation.
