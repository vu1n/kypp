# kypp

A swarm-memory engine for coding agents — **observe → distill → recall → consolidate** over a shared,
code-grounded store, exposed as one MCP server.

Agents capture what happened (a session's §0 event trace), distill durable **claims** (facts,
decisions, procedures, pitfalls — model-agnostic, grounded to code locations), recall them
semantically, and consolidate near-duplicates. Built for a *swarm*: many agents share one store, so
memory becomes the team's accumulated lore rather than one agent's scratchpad.

## Install

```sh
uv run kypp --help          # from this dir — uv builds + installs from pyproject
# or: uv pip install -e .   → `kypp` on PATH
```

## Commands

```sh
kypp serve [--http --port 7077]   # the MCP server (observe/claim/recall/decide/remember_procedure/
                                  #   consolidate/resolve_conflicts) — stdio by default, --http to attach
kypp capture <log.jsonl | ->      # capture one session's §0 log into memory
kypp sweep                        # autocapture: sweep completed sessions (idempotent, cron-friendly)
kypp consolidate [--semantic D]   # dedup near-duplicate claims (exact + optional semantic)
kypp batch                        # LLM re-distill a corpus of logs, one representative per task
```

## Config (env)

| var | meaning |
|---|---|
| `KYPP_MEMORY_DB` | store path (default `~/.kypp/memory.db`) |
| `KYPP_PROJECT` | claim scope (default `default`) |
| `KYPP_REPO_ROOT` | repo the ripgrep code-resolver grounds against (default `.`) |
| `KYPP_EMBED_MODEL` | ollama model → semantic vector recall (unset → keyword) |
| `KYPP_DISTILL_MODEL` | ollama model → LLM distillation (unset → heuristic floor) |
| `KYPP_OLLAMA_HOST` | ollama base URL (default `http://127.0.0.1:11434`) |

## How it plugs in

kypp is **independent** — any MCP client *attaches* to it; it spawns nothing and depends on no host
runtime. With pillbox, for example:

```sh
kypp serve --http --port 7077 &
pillbox run --mcp kypp=http://localhost:7077 -- "…"   # the agent recalls/claims mid-task
kypp sweep                                            # capture the completed session(s)
```

kypp *consumes* §0 session logs (default glob `~/.pillbox/*/sessions/*/log.jsonl`, configurable via
`--logs`) but has no build/runtime dependency on pillbox.

Storage: [tursodb](https://docs.turso.tech/) (embedded — concurrent writes + native vector search).
Code grounding: ripgrep over the live repo (an AST/BM25 index can drop in behind the resolver seam).
Self-tests: `uv run python -m kypp.<module>`.
