# Amend `authority-dominates`: synced Brief decisions carry the authority of whoever ratified them

Proposed 2026-10-11, revised the same day. This replaces the earlier draft, which added an operator
route that wrote `authority=human` for §9's ratify, promote and sync actions. The owner has since
decided that merging an amendment PR is the ratification, and that a System One model ratifies by
default and only escalates reversals and high-impact changes to a person (brief v0.5.0). That moves
every org-level ratification into Brief, so kypp needs one write path, not three.

## Why change

The invariant names one writer of `authority=human`: the local operator's `kypp correct`. Org claims
live in the hosted Worker's D1, where nothing can write above `agent`, so §9's org rules can't
outrank an agent claim. With ratification now done in Brief, the Worker doesn't need to decide who
ratified anything; it only needs to copy a ratified decision in with the authority the ratification
already earned.

## What changes

- **One sync writer, outside MCP.** The Worker gains `POST /sync`, which upserts an accepted org
  claim from a ratified Brief decision: the `doc://` ref pinned to its revision, the one-line rule,
  and the archive's `ratified_by` stamp. It is called only by the org's CI after `brief ratify`
  publishes a revision, authenticated by `KYPP_SYNC_TOKEN`, a Worker secret kept apart from agent
  OAuth grants and `KYPP_API_TOKENS`. With the secret unset, the route answers 404. It is never
  reachable through `/mcp`.
- **Authority follows the ratifier.** A revision a person ratified (their `/ratify`, or their merge)
  syncs at `human`. A revision the model ratified (`ratified_by: s1 ...`) syncs at `verified`: it
  outranks agent guesses but yields to a person, which matches what a model check is. The Worker
  reads this from the stamp; it never takes an authority value from the caller.
- **The rest stays.** No MCP tool passes `authority="human"` or `"verified"`. MCP `correct` still lands
  at agent authority. `kypp verify` still sets `verified` locally. Every sync is logged in
  `defrag_log` with the ref and the stamp it came from.

The sync token can write org rules for every repo under the org, so it lives in `wrangler secret` and
the org's CI secrets, never in a repo, a claim or an agent's environment.


---
ratified_rev: 0004
ratified_by: Vu
