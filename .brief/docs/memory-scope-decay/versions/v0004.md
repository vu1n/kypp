---
id: memory-scope-decay
project: kypp
type: decision
status: active
title: "What kypp remembers, how claims are keyed, and how they are forgotten"
supersedes: docs/decisions/2026-10-04-memory-scope-decay-feature-keys.md
related_code:
  - "kypp/store.py"
  - "kypp/distill.py"
  - "kypp/arbiter.py"
  - "kypp/view.py"
---

<!-- brief:anchor scope-keys-decay -->
## Hold what docs can't, key claims by feature, forget on event clocks

**Status:** proposed 2026-10-04. It builds on the `compose` decision
(`2026-06-21-agent-mcp-surface-compose.md`) and narrows it. This record changes what goes *into* the
store, how claims point at code, and how they leave. `compose` is still the retrieval verb.

## The problem

Stale memory is the failure that matters, and kypp has nothing to stop it:

- **Nothing prunes.** A claim leaves recall only when it is superseded, rejected or fails a `verify`.
  `claim_usages.score`, the outcome signal, is never written.
- **Stale memory misleads agents.** In the Brief eval (vu1n/brief#2, 294 runs), stale memory dropped
  Haiku from 14/18 to 4/18. Sonnet followed it on decisions the code can't show. A one-line "why"
  comment plus a decisions doc restored 15–18/18 *with the stale memory still present*.
- **Code anchors don't detect staleness.** Every stale memory in that eval pointed at code that still
  existed, so "the anchor resolves" said nothing about whether the claim was still true.
  Text-matched anchors (`RipgrepResolver`) also break on a rename.
- **Indiscriminate writes hurt.** Add-all memory scored below a memory that never grew. Selective
  addition plus utility-based deletion gained ~10 points (Xiong et al., arXiv 2505.16067). Background:
  `/mnt/project-files/research/agentic-memory.md` (project files).

## The decision

### 1. Scope: kypp holds what docs can't

Durable code facts belong in the repo, as "why" comments, Brief decisions and feature-map Gotchas.
kypp holds **preferences, process, and work in progress**, plus pitfalls on their way to becoming
docs. On a conflict, in-repo docs win and the claim is marked suspect.

**kypp proposes, docs keep.** A claim that keeps proving true is promoted by a PR into the owning
feature's Gotchas in the Brief feature map. The map is descriptive, so no ratification is needed. Once
the promotion merges, the claim is superseded.

### 2. Write gate

A cheap ("system one") model scores each distilled draft before it becomes a candidate. It keeps only
lessons that are durable, non-obvious and model-agnostic, and that are **not derivable from the
current code or docs**. Anything the code already shows is dropped. Anything the code should show but
doesn't is routed to a docs PR instead.

### 3. Keys: features, not code locations

A claim's primary key is a Brief feature ref, `doc://<project>/features@latest#<feature-id>`
(vu1n/brief#5). The feature file supplies context ("in search, …"), and its `paths:` globs supply the
code. Recall from a diff is `brief features <files> --json` → features → their claims.

A symbol anchor is an optional second key for the rare claim about one function. It stores the
qualified name plus a hash of the normalized syntax tree (whitespace and comments ignored), and is
resolved lazily at recall. This is bacchus's extractor approach (`src/indexer/extractor.rs`), with two
changes: hash the normalized tree, not raw text, and treat the file path as a hint rather than part of
the name. Line numbers are never stored.

### 4. Forgetting: event clocks, not wall time

Nothing ages while nothing happens, so a quiet repo keeps its memory. Each claim class has its own
clock:

| clock | applies to | ticks when |
|---|---|---|
| churn | feature-keyed claims | commits change files under the feature's `paths:` since the claim was written |
| shape | symbol-anchored claims | the symbol's normalized-tree hash differs at recall |
| sessions | preferences / process / WIP | the claim is briefed in a session but goes unused |
| model | tactics, model-specific tips | the agent's model changes |

When a clock passes its threshold, the claim becomes **suspect**. It is not deleted.

### 5. A cheap judge, triggered by events

A suspect claim goes to a cheap model along with the evidence (the diff since the claim was written,
or the changed symbol), which returns one of three verdicts:

- **Still true:** the clock resets.
- **Unsure:** the claim is flagged.
- **Contradicted:** the claim goes dormant.

The same model judges each briefed claim at session end as *used / ignored / contradicted*. That
finally writes `claim_usages.score`. Cost scales with churn and activity, not with store size.

### 6. Lifecycle

`candidate → accepted → suspect → dormant → archived`, alongside the existing `superseded` and
`rejected`.

- **Dormant** drops out of recall and briefing, but `expand` still works.
- **Archived** is cold.
- **Nothing is hard-deleted** on a timer.

Human-corrected and verified claims decay more slowly.

### 7. Feature renames are kypp's problem, not Brief's

Brief only flags broken refs in code, so a renamed or deleted feature id silently orphans kypp claims.
On `sweep`, kypp diffs the current `brief features --json` against the last one it saw. Claims whose
feature vanished are re-pointed in the same pass, with the judge picking the successor from that
commit's map diff, or marked suspect. The old→new mapping lives in kypp and decays like any claim.

Brief stays clean: no aliases or tombstones in the doc every agent reads.

### 8. Placement: write at origin, read by cascade, arrange in defrag

Amended 2026-10-07 (ratified by Vu). The hosted alpha showed that asking the agent to choose where a
memory belongs fails the same way a prompt hint does: Claude, Codex and Claude projects connect
without a project header, and an unknown name such as "dev" silently started a separate memory. At
write time an agent knows where it learned something, not how general it is.

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
     those stay with a human (§9 is the only route to an org: a decision ratified in Brief). The origin is kept forever, so any move can be undone.
   - *Decay:* runs the event clocks of §4 and the judge of §5 unchanged.
   - Works per changed subject, not a full rescan, and logs every move with its reason and score.
5. **What the model may do.** The S1 model never accepts or widens anything by itself. Recurrence
   accepts and widens, and the model can only hold either back. Global stays out of automatic
   widening. (Before 2026-10-07 the rule was that the model never moved a claim between scopes.)

Unchanged by this section: append-only history (`never-delete`): superseded and narrowed claims keep expanding by handle. The
§1 boundary (kypp holds what docs can't), feature keys (§3), event clocks (§4), and the user scope as
a separate "who" axis are unchanged. Promotion stays automatic with no owner review, and conflicted
claims wait, per the 2026-10-06 project decision.

Order of work:

0. Unsorted writes never fail, plus the defrag pass filing them into origins, together (unsorted alone
   just builds a pile). Reads label level and status.
1. Add the roll-up table and cascading reads.
2. Cross-subject dedupe and recurrence-based widening in the cron, once real multi-repo lessons exist
   to tune on.

### 9. An org level for how an org should work

Amended 2026-10-11 (ratified by Vu), and again the same day: org rules are ratified in Brief and kypp
only syncs them. The `user` axis holds how one person works and reaches only that person's sessions. A
practice an org expects of everyone in its repos ("open PRs as drafts and run checks in the session
first, because private CI minutes are capped") had no home: project claims can't cross a person
boundary, and `user` claims never widen.

1. **An org is a group at the top of the roll-up.** It is a row in `project_parents` named for the
   GitHub owner (for example `vu1n`), with every repo under that owner as a child. No new scope value:
   an org claim is `scope='group'` with the org's name in `project`, so cascading reads already return
   it to every session whose project rolls up to the org, whoever the user is.
2. **Brief is the only door to the org.** An org rule is a Brief decision in the org's decisions repo
   (one repo per org). It is ratified the Brief way: merging its PR ratifies it, and a System One
   model ratifies by default, escalating reversals and high-impact rules to a person. Agents never
   write to the org: the `claim` tool keeps `project` and `user` only, and an agent may at most draft
   a decision PR.
   - *Cross-user recurrence opens a PR.* When defrag finds the same `user` lesson from two or more
     distinct users under one org, it opens a draft decision PR in the org's decisions repo citing the
     claims. The S1 "same lesson?" scorer can hold a proposal back but never cause one.
   - *One-person orgs* use the same path: promoting a `user` claim to the org is opening that PR.
3. **Sync is the only kypp write.** After `brief ratify` publishes a revision, the org's CI calls the
   Worker's sync route (`authority-order`). The claim holds the pinned `doc://` ref and the one-line
   rule, never the decision body, so Brief stays the source of truth. A person-ratified revision lands
   at `human`, a model-ratified one at `verified`; a new revision re-syncs, and a retired or
   superseded doc supersedes its claim.
4. **Reads.** The briefing lists accepted org claims under their own heading ("how this org
   works"), ahead of the user's own process, because an org rule applies to every contributor.
   Recall labels org lines with the group's level. A conflict between an org claim and a user claim
   on one subject is settled by `authority-order`, as today.
5. **Decay.** Org claims run the `sessions` clock of §4 like other process claims. A synced decision
   claim is superseded when its doc is retired or superseded, never by the clock alone.

Unchanged: agents pick only `project` (implicitly) or `user`; widening stays one level at a time and
never reaches global automatically; history stays append-only.

Open: how org membership is known (the repos a user's sessions write in, which needs no new access,
or the GitHub org's members via the OAuth identity); the proposal threshold (two distinct users to
start).

Order of work for §9:

0. The "how this org works" briefing section (done).
1. Decision sync.
2. Defrag opening cross-user decision PRs, once two users' process claims exist to tune on.

## Considered and rejected

- **Wall-clock TTL** (Copilot's 28-day expiry). A quiet repo would lose good memory for no reason.
- **Line-number or text anchors.** These break under agentic churn, and the Brief eval showed that
  even a resolving anchor doesn't mean the claim is still fresh.
- **Commit-time symbol index.** It costs on every commit. Lazy resolution only pays for claims that
  are actually recalled.
- **Aliases/tombstones in the Brief map.** They become permanent debt in a shared doc, for the
  benefit of kypp alone.
- **A general move/delete changelog.** Git rename detection plus the empty-glob gate already make
  `git log` on the map that log.

## Open

- Thresholds for each clock and the judge's model choice. Measure them in the lift test rather than
  guessing.
- The local vs cloud store question (`turso.sync` in pyturso 0.8, or hosted) is separate and not
  decided here.
- Similarity for cross-subject dedupe: S1 pairwise "same lesson?" within an origin, or embeddings
  (Workers AI plus Vectorize). Measure on real claims first.
- Short-lived notes ("deploy is broken right now") are out of scope. The gate rejects them as status.
  If agents want them, they become their own kind with an expiry that defrag never promotes.
- Tags for topical recall boost, never for visibility. Deferred until search misses show the need.
