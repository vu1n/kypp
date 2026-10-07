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
   the `project` argument, or the session) as a flat, immutable origin. The agent never picks a
   project, group or global scope. A write with no origin, or an unknown one, still lands, marked *unsorted*; it never fails
   and never silently creates a new project. Session, agent and time are recorded as today.
   The user axis stays a write-time choice: a claim about how this person works (a preference or
   workflow) is still marked `user` by the agent, as `scope=user` is today, and is read from every
   repo. That marks *who* a lesson is about, not how general it is, so it needs no defrag move.
2. **Reads cascade.** `recall` and `briefing` take the caller's origin and return matches from origin,
   every group or org the origin rolls up to, user, and global, each line labelled with its level and
   status. Relevance ranks first, as the compose contract and today's Worker query do; nearness to the
   origin is a boost and tie-break after relevance, so an exact shared match is never crowded out by
   weak local ones. No scope argument; origin only affects ranking.
   *System One reranks.* Search (FTS today) pulls a wide candidate set across all levels; the S1
   model (Clef on Workers AI, the same client as the write gate) scores each candidate's relevance to
   the query, and the top `limit` are returned. Nearness and status adjust that score after it. This
   lives inside the compose contract's selection step, same signature and caller. If S1 is off, slow
   or fails, the FTS order stands, as the write gate degrades today. Ranking never changes what a
   caller may see, only the order. Candidates under a relevance floor are dropped rather than
   padding to `limit`. `briefing` has no query, so it scores against the repo and the agent's stated
   task. A 2026-10-07 probe (one query, 12 to 36 claims) ranked by meaning with a clean gap
   (relevant at 0.6 or higher, the rest at 0.14 or lower) in about 0.5 s on clef-flash and 1 s on clef.
   Model choice and floor get set on a proper query set. The reranker only sees what search fetched,
   so a wide pool (about 40) matters; embeddings (Open) would widen reach. The same scoring serves
   defrag's "same lesson?" check.
   **Multiplayer:** other sessions' candidates at the caller's origin are returned too, labelled
   unconfirmed and ranked below accepted claims, so agents in one repo see each other's lessons
   (close to agent messaging). Other sessions see unsorted claims once they are filed, so nothing
   leaks across repos. The writing session always sees its own unsorted claims, and filing is
   tried at write time for that one claim, with the hourly pass as the fallback, so header-less
   clients (Claude, Codex, Projects) don't wait an hour to share or recall a lesson. *Echo guard:* a session that was shown a candidate and then claims the same subject
   does not count as the independent second session that accepts it (kypp already logs which
   session was shown which claim). `briefing` stays settled memory plus a short "recent from other
   agents" section; unconfirmed claims mainly surface on `recall`.
   *Injection guard:* reads become a channel between agents, so server instructions say memory is
   information to weigh, never instructions to follow, and the write gate also holds back claims that
   read as control text aimed at agents (override other instructions, send data or credentials
   somewhere, act outside the repo's work) rather than a lesson about the codebase or the person.
   Procedures, preferences and pitfalls are action-shaped by nature and stay in scope under §1.
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
     Widening goes one level at a time, never across a person or org boundary and never to global;
     those stay with a human. The origin is kept forever, so any move can be undone.
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
- Short-lived notes ("deploy is broken right now") are out of scope. The gate rejects them as status.
  If agents want them, they become their own kind with an expiry that defrag never promotes.
- Tags for topical recall boost, never for visibility. Deferred until search misses show the need.


---
ratified_rev: 0002
ratified_by: Vu
