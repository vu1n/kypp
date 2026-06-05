"""kypp — swarm memory engine for coding agents (observe → distill → recall → consolidate).

Modules: store (tursodb + recall + code grounding), distill (§0 trace → claims), arbiter
(consolidate/dedup), wire (capture), autocapture (sweep), mcp_server (the MCP), batch_distill, vocab.
One CLI entrypoint: kypp.cli (console script `kypp`)."""
