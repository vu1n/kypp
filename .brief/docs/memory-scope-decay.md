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
