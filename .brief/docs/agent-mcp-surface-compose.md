---
id: agent-mcp-surface-compose
project: kypp
type: decision
status: active
title: "Agent MCP surface: trim to compose / claim / expand / correct"
supersedes: docs/decisions/2026-06-21-agent-mcp-surface-compose.md
related_code:
  - "kypp/mcp_server.py"
  - "worker/src/mcp.ts"
  - "worker/src/cooperation.ts"
  - "worker/src/cooperation-store.ts"
  - "worker/src/cooperation-query.ts"
---

<!-- brief:anchor compose-surface -->
## Trim the agent MCP surface to compose, claim, expand, correct

**Status:** proposed 2026-06-21. The interface decision (verb trim) and the *bounded* contract stand on
their own. Two empirical pieces, different confidence:
- **Dump-pollutes — robust.** Across every aider held-out run, injecting the full accepted store scores
  *below* baseline (a cheap model drowns in an unselected dump). This justifies `compose`'s `limit` bound.
- **Task-conditioned selection *recovers* the loss — NOT established.** A single-trial smoke looked great
  (`bottle_song` recall-semantic 1.0) but did not survive replication (0.05 over 3 trials — the held-out
  scores are noise-dominated at low n), and several held-out tasks sit at baseline *ceiling* (no headroom
  for memory to help). The recovery claim needs a **headroom-controlled** paired test (tasks whose baseline
  is genuinely mid-range) before it is load-bearing. What is settled: compose must SELECT, not dump.

## The problem

The agent-facing surface is the MCP server (`kypp/mcp_server.py`), not the CLI — an agent reasons over
*every* tool description on *every* call, so verb count is a per-call reasoning tax there in a way it is
not for a human at a CLI. The MCP currently exposes **10 tools**:

`observe`, `claim`, `recall`, `expand`, `briefing`, `correct`, `decide`, `remember_procedure`,
`consolidate`, `resolve_conflicts`.

They cluster on three axes, and most of the tail is redundant or off the agent's path:

- **retrieve** — `briefing` is `recall` with no query; both answer "what should I know?"
- **record** — `decide` / `remember_procedure` are `claim(type=decision|procedure)`; `observe` is the
  raw-signal layer the wire/distill pipeline owns, not something an agent should hand-author.
- **curate** — `consolidate` / `resolve_conflicts` are cron/human maintenance (consolidate already runs
  on a schedule); only `correct` is an agent-time op.

The agent's real hot loop is 4 verbs (`briefing → recall → claim → expand`). The other 6 add *decisions
without power*: every near-synonym is a "which one?" the agent resolves mid-task (`observe`-vs-`claim`,
`claim`-vs-`decide`-vs-`remember_procedure`) instead of doing the work. The INSTRUCTIONS protocol
(`mcp_server.py:31`) is good prose, but it has to disambiguate those overlaps inline.

## The decision

Trim the agent MCP surface to **4 orthogonal verbs**, anchored on a new `compose` that unifies retrieval:

```python
compose(task="", limit=12, scope=None, types=None, verbose=False) -> str | list[dict]
claim(subject, content, type="fact", confidence=0.7,
      scope="project", source_ids=None, code_refs=None) -> str
expand(handle) -> dict
correct(subject, content, type="fact") -> str
```

| verb | does | replaces |
|---|---|---|
| `compose` | the right, **bounded**, grounded context for a task | `briefing` + `recall` |
| `claim`   | save one durable lesson | `claim` + `decide` + `remember_procedure` |
| `expand`  | dereference a handle → full claim | `expand` |
| `correct` | propose a human-reported fix as a candidate at agent authority; operator correction is a separate capability | `correct` |

Moved off the agent surface: `observe` → wire/distill pipeline; `decide`/`remember_procedure` →
`claim`'s `type`/`accept`; `consolidate`/`resolve_conflicts` → CLI/cron.

## `compose` — the keystone

`task=""` → the session-start digest (strongest accepted, pitfalls first — the old `briefing`).
`task=<text>` → the top-`limit` claims semantically relevant to the task (the old `recall`, now genuinely
task-conditioned once claims carry embeddings).

Two semantics are load-bearing:

1. **`limit` is the anti-pollution bound, not cosmetic.** A full-store dump hurts a cheap model more than
   it helps — measured robustly on the aider held-out set: injecting all 41 accepted claims scores below
   baseline across runs (context pollution). Whether a task-conditioned top-k *recovers* that loss is not
   yet established (noise-dominated at low trial counts; needs headroom-controlled tasks — see Status).
   But the directional lesson is settled and sufficient to motivate the contract: `compose` must SELECT,
   not dump; the default `limit=12` means "a digest, not the store."
2. **It is the Kimi seat.** v1 selection is `semantic-recall-then-cap`. When the corpus outgrows a cheap
   scan, the *internals* of `compose` become `wide-read → compress to a budget` (a long-context model,
   e.g. Kimi-Linear) — **same signature, same caller**. The growth params (`budget_tokens` to compress to
   a token target rather than a claim count; `role` to bias planner-vs-implementer-vs-reviewer selection)
   belong here but are deliberately out of v1, so the agent's call stays `compose("fix the refresh-token
   bug")`.

## `claim` — absorbing the record sugar

Every MCP claim, including a decision or reported correction, lands a candidate. No MCP acceptance
flag or type self-accepts. Automatic admission requires non-echo recurrence and established agreement;
explicit acceptance and human correction are operator commands. Pending candidates may still appear
as labelled unconfirmed contributions where the scope decision permits. `observe` leaves the agent
surface entirely: agents claim *distilled* lessons; raw observations are the capture pipeline's job.

## The protocol an agent then reads (1:1 with the verbs)

```
1. Before non-trivial work → compose("<what you're about to touch>"). Empty task = the session digest.
2. Learn something durable → claim(subject, content). It is a candidate until accepted.
3. Acting on a handle → expand(handle) for the full claim + live code pointer.
4. A human says a memory is wrong → correct(subject, content). Propose the fix; only operator `kypp correct` asserts human authority.
```

vs the current 4-step protocol, which must disambiguate `briefing`-vs-`recall` and
`claim`-vs-`decide`-vs-`remember_procedure` inline.

## Migration

The claim-state-machine migration changes acceptance semantics independently of the future compose
verb trim. Existing MCP names can remain compatibility aliases, with descriptions/results that clearly
report candidate corrections. The operator CLI retains explicit acceptance and human correction.
Versioned domain commands replace unrestricted status writes; the bounded compose implementation
remains separate retrieval work.

## Hosted cooperation surface

Amended 2026-10-13 (ratified by Vu). A separate, narrow hosted cooperation surface is permitted
alongside durable memory. This does not claim `compose` is already implemented, and it does not
require the four-verb migration above to land with cooperation records. The Python backend and
the local MCP surface are not expanded in this first slice. Existing durable claim tooling and
authority stay intact.

- `append_context` appends one versioned client-authored handoff or evidence-reference record.
- `query_context` returns bounded, source-linked records selected under current authorization.
  Exact-ID historical access is a bounded mode of this same operation.

These operations supplement today's briefing, recall, claim, expand and correct implementation.
`append_context` and `query_context` take no authority parameter. `/sync`, `correct` and
`mark_verified` are unchanged under `authority-order@0006`.

Kypp may filter, rank and retrieve by task relevance, including semantic selection later. Its
output is a memory contribution. It does not own role profiles, required human intent, invocation
construction, a sealed HCP, task assignments, task completion, approvals or agent execution. The
caller authors the handoff and assembles its own final context. Future role bias or compression
in `compose` remains a retrieval design question, not a Kypp invocation-compiler mandate.

For v1, the record, privacy and lifecycle contract is `memory-scope-decay` §10. Enforce input
bounds, a maximum 100-record authorized candidate window, at most 30 returned records and at most
32 KiB of serialized contribution including its selection manifest. Record selected IDs and
revisions, cutoff, selector version and safe bounded omission information. Never dump the whole
store, truncate away provenance, or rank inaccessible records. The wire document is a later
freeze; it is not this revision.

The initial implementation uses deterministic exact and lexical selection and no paid model.
Semantic selection can improve ranking later without changing authority, source visibility or
response bounds. It must never silently widen the audience.

Not on this surface: inbox delivery guarantees, presence, acknowledgments, locks, a workboard
task database, scheduler, background wake loop, automatic status synchronization or an Advisor.
A workflow reference points to the system that owns its task state.

## Considered and rejected

**Keep the 10, lean harder on INSTRUCTIONS.** Rejected: prose cannot remove the per-call "which tool?"
decision that overlapping verbs force on the model — only collapsing the verbs does. The ergonomic win
(10 → 4; a mental model an agent holds after one read) and the new capability (`compose` = the bounded
selection the pollution finding shows is *necessary*, and the natural Kimi insertion point) are the same
move, so they ship together.
