"""Shared vocabulary for the swarm-memory engine — the closed sets store.py and distill.py both
validate against. A turso-free leaf (imports nothing heavy) so distill/mcp can pull it without the
store's engine dep; single-sourced here so the two can't drift — a drift between them is a SILENT
correctness bug (a type valid in one but rejected by the other).

The Literal types are the source; the tuples are derived. MCP tool signatures annotate with the
Literals so the closed set lands in the tool's JSON schema (the client model can't guess an invalid
value — the schema rejects it before the store's runtime check ever fires)."""
from typing import Literal, get_args

Scope = Literal["project", "global"]  # reach: this repo, or cross-project. Authorship is a separate axis (the agent/user provenance columns), NOT a scope.
ClaimType = Literal["fact", "preference", "decision", "procedure", "artifact", "hypothesis", "pitfall"]
Status = Literal["candidate", "accepted", "superseded", "rejected"]  # claim lifecycle (spec status field)
# WHO asserted a claim — the highest-signal, variance-free scoring channel: a human correction
# outranks any agent claim (and any amount of agent corroboration), a deterministically-verified
# claim outranks an agent guess. Ordered low→high; AUTHORITY_RANK is the survivor tie-break.
Authority = Literal["agent", "verified", "human"]
# How a claim was surfaced to a consumer — the closed set usage provenance records (recall/briefing
# read paths; expand dereferences a handle). Single-sourced so a new read surface picks from the set.
Surface = Literal["recall", "briefing", "expand"]

SCOPES: tuple[str, ...] = get_args(Scope)
TYPES: tuple[str, ...] = get_args(ClaimType)
STATUSES: tuple[str, ...] = get_args(Status)
AUTHORITIES: tuple[str, ...] = get_args(Authority)
AUTHORITY_RANK: dict[str, int] = {a: i for i, a in enumerate(AUTHORITIES)}
SURFACES: tuple[str, ...] = get_args(Surface)

# Confidence a human correction lands at — high, but the AUTHORITY tier (not this number) is what makes
# it win ranking, so the exact value is secondary signal, not the dominating one. Single-sourced so the
# CLI default and the MCP `correct` tool can't drift.
HUMAN_CORRECTION_CONFIDENCE = 0.95

OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"  # shared default: ollama_embed (store) + ollama_complete (distill)
