# Agent MCP surface: trim to `compose` / `claim` / `expand` / `correct`

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
claim(subject, content, type="fact", accept=False, confidence=0.7,
      scope="project", source_ids=None, code_refs=None) -> str
expand(handle) -> dict
correct(subject, content, type="fact") -> str
```

| verb | does | replaces |
|---|---|---|
| `compose` | the right, **bounded**, grounded context for a task | `briefing` + `recall` |
| `claim`   | save one durable lesson | `claim` + `decide` + `remember_procedure` |
| `expand`  | dereference a handle → full claim | `expand` |
| `correct` | human override (authoritative) | `correct` |

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

`claim` lands a CANDIDATE (invisible to `compose` until ≥2 sessions corroborate — the swarm-truth gate)
unless `accept=true` or `type="decision"` (which auto-accepts in the store). So
`decide(s,c)` ≡ `claim(s,c,type="decision")` and `remember_procedure(s,c)` ≡
`claim(s,c,type="procedure",accept=True)`. The `accept` flag plus a one-line description carry what two
extra verbs did. `observe` leaves the agent surface entirely: agents claim *distilled* lessons; raw
observations are the capture pipeline's job.

## The protocol an agent then reads (1:1 with the verbs)

```
1. Before non-trivial work → compose("<what you're about to touch>"). Empty task = the session digest.
2. Learn something durable → claim(subject, content). accept=true / type=decision for settled truths.
3. Acting on a handle → expand(handle) for the full claim + live code pointer.
4. A human says a memory is wrong → correct(subject, content). It outranks everything.
```

vs the current 4-step protocol, which must disambiguate `briefing`-vs-`recall` and
`claim`-vs-`decide`-vs-`remember_procedure` inline.

## Migration

Presentation-layer only — no data-model change. `compose` = `recall` + `briefing` composed (both exist);
`claim(accept=…)` already exists. Keep every current verb as a **CLI** command (humans and scripts
tolerate a rich surface). Optionally leave `recall` / `briefing` / `decide` / `remember_procedure` as
deprecated MCP aliases for one release. The only thing to *build* is `compose`'s bounded, task-conditioned
selection — which the `kypp-recall` arm in the pillbox eval is already hand-rolling against the embedded
claim store.

## Considered and rejected

**Keep the 10, lean harder on INSTRUCTIONS.** Rejected: prose cannot remove the per-call "which tool?"
decision that overlapping verbs force on the model — only collapsing the verbs does. The ergonomic win
(10 → 4; a mental model an agent holds after one read) and the new capability (`compose` = the bounded
selection the pollution finding shows is *necessary*, and the natural Kimi insertion point) are the same
move, so they ship together.
