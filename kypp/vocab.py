"""Shared vocabulary for the swarm-memory engine — the closed sets store.py and distill.py both
validate against. A turso-free leaf (imports nothing heavy) so distill/mcp can pull it without the
store's engine dep; single-sourced here so the two can't drift — a drift between them is a SILENT
correctness bug (a type valid in one but rejected by the other)."""

SCOPES = ("user", "project", "agent", "global")
TYPES = ("fact", "preference", "decision", "procedure", "artifact", "hypothesis", "pitfall")
STATUSES = ("candidate", "accepted", "superseded", "rejected")  # claim lifecycle (spec status field)
OLLAMA_DEFAULT_HOST = "http://127.0.0.1:11434"  # shared default: ollama_embed (store) + ollama_complete (distill)
