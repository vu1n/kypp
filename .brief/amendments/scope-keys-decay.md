# Amend `scope-keys-decay`: agents write at origin; a "defrag" pass places, merges and decays

Proposed 2026-10-07, from Vu in the dev project thread: "agent just writes to memory; reads return
memories with different scopes matching the query; a periodic consolidation process dedupes,
organizes, arranges."

## Why change

The hosted alpha showed that asking the agent to choose where a memory belongs fails the same way a
prompt hint does. Claude, Codex and Claude projects connect without a project header, so the agent has
to name a project on every call, and an unknown name such as "dev" silently starts a separate memory.
At write time an agent knows where it learned something, not how general it is. Placement belongs in a
deterministic background pass, not in the agent's call.

## What changes

1. **Writes record origin, never scope.** A claim stores the repo it was learned in (from the header,
   the `project` argument, or the session) as a flat, immutable origin. The agent never passes a
   scope. A write with no origin, or an unknown one, still lands, marked *unsorted*; it never fails
   and never silently creates a new project. Session, agent and time are recorded as today.
2. **Reads cascade.** `recall` and `briefing` take the caller's origin and return matches from origin,
   then every group or org the origin rolls up to, then user, then global, nearest first, each line
   labelled with its level and status. No scope argument; origin only affects ranking.
   **Multiplayer:** other sessions' candidates at the caller's origin are returned too, labelled
   unconfirmed and ranked below accepted claims, so agents in one repo see each other's lessons
   (close to agent messaging). Unsorted claims appear once defrag has filed them, so nothing leaks
   across repos. *Echo guard:* a session that was shown a candidate and then claims the same subject
   does not count as the independent second session that accepts it.
3. **Roll-up lives in a table, not in claims.** A small, editable `project → parent(s)` table (a repo
   may sit in several groups) defines what each origin can see. Regrouping never rewrites a claim.
4. **The defrag pass (the existing hourly consolidation cron, extended) does the arranging:**
   - *File:* assigns unsorted claims to an origin (an S1 choice over the known projects). Unsure
     claims stay unsorted.
   - *Dedupe:* finds near-duplicate claims across subjects, not only exact-subject groups, and
     supersedes the losers. The survivor is an existing claim. The pass never writes new memory text.
   - *Place:* widens a claim's visibility from origin to a group or org when the same lesson recurs
     from two or more origins under that parent (recurrence, the rule that already accepts claims,
     applied one level up). The S1 `general` score may hold a widening back, never cause one.
   - *Decay:* runs the event clocks of §4 and the judge of §5 unchanged.
   - Works per changed subject, not a full rescan, and logs every move with its reason and score.
5. **Replaced invariant.** §2 and the Worker gotcha say the model "never accepts a claim or moves it
   between scopes". This becomes: the S1 model never accepts or widens anything by itself. Recurrence
   accepts and widens, and the model can only hold either back. Global stays out of automatic
   widening.

## What stays

Append-only history (`never-delete`): superseded and narrowed claims keep expanding by handle. The
§1 boundary (kypp holds what docs can't), feature keys (§3), event clocks (§4), and the user scope as
a separate "who" axis are unchanged. Promotion stays automatic with no owner review, and conflicted
claims wait, per the 2026-10-06 project decision.

## Order of work if ratified

0. Unsorted writes never fail, plus the defrag pass filing them into origins, together (unsorted alone
   just builds a pile). Reads label level and status.
1. Add the roll-up table and cascading reads.
2. Cross-subject dedupe and recurrence-based widening in the cron, once real multi-repo lessons exist
   to tune on.

## Open

- Similarity for cross-subject dedupe: S1 pairwise "same lesson?" within an origin, or embeddings
  (Workers AI plus Vectorize). Measure on real claims first.
- Tags for topical recall boost, never for visibility. Deferred until search misses show the need.
