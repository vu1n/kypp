# kypp

A swarm-memory engine for coding agents — **observe → distill → recall → consolidate** over a shared,
code-grounded store, exposed as one MCP server *and* a plain Bash CLI.

Agents capture what happened (a session's §0 event trace), distill durable **claims** (facts,
decisions, procedures, pitfalls — model-agnostic, grounded to code locations), recall them
semantically, and consolidate near-duplicates. Built for a *swarm*: many agents share one store, so
memory becomes the team's accumulated lore rather than one agent's scratchpad.

## Install

```sh
uv tool install .           # → `kypp` on PATH (what agents shell out to)
# or from this dir: uv run kypp --help
```

## For agents

You are a coding agent with access to kypp, the team's shared memory. Everything below works over
two equivalent surfaces — pick whichever you have:

- **Bash** (zero setup beyond `kypp` on PATH): `kypp briefing | recall | show | remember`
- **MCP** (richer; structured): attach via stdio — `claude mcp add kypp -- kypp serve` — or to a
  shared HTTP server: `kypp serve --http --port 7077`, then point your client at
  `http://localhost:7077` (from a sandbox: `host.docker.internal`). Set `KYPP_REPO_ROOT` to the repo
  where the server runs — it binds one project and grounds against one repo. The project key is
  **derived from that repo path** (so live recall/claim share a bucket with `sweep`'s captured
  claims); set `KYPP_PROJECT` only to override it. On connect the server hands the agent the protocol
  below as MCP instructions, so an attached client knows the workflow without reading this README.

### The protocol

1. **Session start — load the lore.** Call `briefing` (MCP) or `kypp briefing --project <name>`
   once before working. It returns the project's strongest accepted memory, known traps (pitfalls)
   first. No query needed; skipping it means re-discovering pitfalls the team already paid for.
2. **Before non-trivial work — search.** `recall("<what you're about to touch>")`. Results are one
   line per claim: `handle [type ✓conf] subject — content → path:line`. The 8-char **handle** is a
   pointer — `expand(handle)` / `kypp show <handle>` dereferences the full claim (unclipped content,
   provenance, live code grounding). Only expand what you act on; the line is usually enough.
   Reading the marks: `✓` accepted / `?` candidate; `👤` human-corrected and `☑` verified outrank
   agent claims — trust them over your own inference. `⚠ code gone (stale)` means every code anchor
   failed to resolve (advisory — the lesson may still hold). The `path:line` pointer is resolved
   against the *current* tree — read the code yourself, memory never inlines file content.
3. **When you learn something durable — write it.** `claim`/`remember` a distilled lesson, not a
   transcript. The rules that make it useful to the next agent:
   - **`subject` is the claim's identity.** Reuse an existing subject to update/correct it
     (consolidation keeps the strongest version); a new subject creates a new memory. Short noun
     phrase, not a sentence.
   - **Keep content model-agnostic.** Memory is shared across models; "claude couldn't X" degrades
     transfer — write "X fails when…".
   - **Anchor to code** via `code_refs [{symbol, path, query}]` when the lesson concerns specific
     code. Anchors re-resolve at recall, so they survive refactors.
   - **Candidate vs accepted:** plain claims land as *candidates* — visible to `recall
     include_candidates=true`, **invisible to `briefing` and default recall**. For settled team
     truths use `decide` / `remember_procedure` (MCP) or `kypp remember --accept`.
4. **Wrong memory — correct it, don't ignore it.** Two cases:
   - *A human told you the right answer* → `correct(subject, content)` (MCP) / `kypp correct`. It
     records with **human authority** — outranking any agent claim and any amount of agent
     corroboration — and immediately supersedes the subject's other claims. Reserve it for actual
     human input.
   - *You believe it's wrong* → write the counter-claim under the **same subject** with higher
     confidence; `consolidate` supersedes the loser.
   Nothing is ever deleted; superseded claims stay as history (handles still `expand` — check
   `status` before trusting one from old context).

### Host side (capture & maintenance — operator, not agent)

The write side runs without any agent cooperation: `kypp sweep` captures every completed §0 session
log (idempotent, cron-friendly), deriving each session's project from its pillbox path; `kypp
capture <log.jsonl>` does one. With `KYPP_DISTILL_MODEL` set, captured traces are distilled into
claims by a local LLM (heuristic failure-mining is the fallback floor).

Cron-safe maintenance alongside `sweep`:

- `kypp verify` — re-runs claims' attached `verify` shell checks (exit 0 = still true): pass →
  accepted + `☑ verified` authority; fail → rejected (revives when the check passes again).
  **Trust boundary:** verify commands are arbitrary shell — attach them yourself (`kypp remember
  --verify 'cmd'` / `kypp correct --verify`), and never run `kypp verify` against a shared store
  whose claims you didn't vet. An operator capability; agents shouldn't mint verifiers unattended.
- `kypp consolidate [--semantic D]` — dedup/supersede near-duplicates.
- `kypp usage --record --session ID --claim H…` — record which claims a run was shown (the
  run-loop hook); read back with `kypp usage --session ID` / `--claim H` for outcome attribution.

## Commands

```sh
kypp serve [--http --port 7077]   # the MCP server — stdio by default, --http to attach
kypp recall "libkrun docker"      # search memory → compact lines with handles
kypp show a1b2c3d4                # expand a handle → the full claim (JSON)
kypp remember "subject" "lesson"  # store a claim (subject = identity key; --accept for team truth)
kypp correct "subject" "answer"   # human correction: outranks + supersedes the subject's claims
kypp briefing [--candidates]      # session-start digest, pitfalls first (--candidates: own retries too)
kypp verify                       # re-check verifier-carrying claims → verified / rejected
kypp usage [--record]             # provenance: which claims a run saw / which runs saw a claim
kypp capture <log.jsonl | ->      # capture one session's §0 log into memory
kypp sweep                        # autocapture: sweep completed sessions (idempotent, cron-friendly)
kypp consolidate [--semantic D]   # dedup near-duplicate claims (exact + optional semantic)
kypp batch                        # LLM re-distill a corpus of logs, one representative per task
```

## MCP tools

`observe` · `claim` · `recall` · `expand` · `briefing` · `correct` · `decide` ·
`remember_procedure` · `consolidate` · `resolve_conflicts`

The DX contract is **handles**: `recall`/`briefing` return one compact line per claim —
`handle [type ✓conf] subject — content → code pointer` — and `expand(handle)` dereferences only
what the agent acts on, so depth is paid per-use, not per-search. Code grounding works the same
way: claims carry durable anchors, recall resolves them to live `path:line` pointers (never file
content). `briefing` is the push side — call it once at session start (or wire it into a host
hook) to load the project's pitfalls and decisions before the agent has a query.

## Memory model

A **claim** is `{type, subject, content, scope, status, confidence, authority, source_ids,
code_refs, agent?, user?, verify?}` (`agent`/`user` = authorship provenance, stamped on every claim).
Types: `fact | preference | decision | procedure | artifact | hypothesis | pitfall`.
Scopes: `project` (this repo) · `global` (cross-project, all see it). Memory is shared and recall is
author-blind; the `agent`/`user` columns are authorship **provenance** (who/what wrote it), surfaced on
`expand` — not a visibility tier.
Lifecycle: `candidate → accepted | superseded | rejected` — recall never returns
superseded/rejected, prefers accepted, and nothing is ever deleted.
Authority: `agent < verified < human` — the survivor tie-break; a human correction beats any
amount of agent corroboration, a `verify`-confirmed fact beats an agent guess. `stale` is an
advisory flag set at recall when a code-anchored claim's anchors all fail to resolve.
**Observations** are the raw append-only layer underneath (provenance for distilled claims).

## Config (env)

| var | meaning |
|---|---|
| `KYPP_MEMORY_DB` | store path (default `~/.kypp/memory.db`) |
| `KYPP_PROJECT` | project binding; unset → **derived from `KYPP_REPO_ROOT`** (its path as a `/`→`-` key, matching what `sweep` files under) so serve + CLI + capture share one bucket per repo; `sweep`/`capture` instead derive each session's project from its pillbox log path (`global/` vs `projects/<key>/`) |
| `KYPP_REPO_ROOT` | repo the ripgrep code-resolver grounds against, and the source of the derived `KYPP_PROJECT` key (default `.`) |
| `KYPP_USER` | authoring human, stamped on every claim as provenance (default: OS login) |
| `KYPP_AGENT` | authoring agent/model label (e.g. `claude-code`), stamped on every claim as provenance (unset → none) |
| `KYPP_EMBED_MODEL` | ollama model → semantic vector recall (unset → keyword) |
| `KYPP_DISTILL_MODEL` | ollama model → LLM distillation (unset → heuristic floor) |
| `KYPP_OLLAMA_HOST` | ollama base URL (default `http://127.0.0.1:11434`) |

## How it plugs in

kypp is **independent** — any MCP client *attaches* to it; it spawns nothing and depends on no host
runtime. With pillbox, for example:

```sh
kypp briefing --project myproj    # host-side: inject the digest into the agent's prompt
pillbox run --mcp kypp=http://localhost:7077 -- "…"   # optional: mid-task recall/claim via MCP
kypp sweep                        # capture the completed session(s), per-path projects
```

kypp *consumes* §0 session logs (default source: `~/.pillbox/global/sessions/` +
`~/.pillbox/projects/*/sessions/`, override via `--logs`) but has no build/runtime dependency on
pillbox.

Storage: [tursodb](https://docs.turso.tech/) (embedded — concurrent writes + native vector search).
Code grounding: ripgrep over the live repo (an AST/BM25 index can drop in behind the resolver seam).
Self-tests: `uv run python -m kypp.<module>`.
