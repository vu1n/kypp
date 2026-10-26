# kypp feature map

What each part of kypp does, where its code and tests are, and what bites. It describes the code as
it is: edit it freely, in the same PR as the code it describes. The format is the `feature-map`
skill's (vu1n/dev).

```sh
python3 scripts/featuremap.py features --base origin/main   # the features this branch touches
python3 scripts/featuremap.py check                         # CI: every pointer still resolves
```

| Feature | What it is |
| --- | --- |
| [claim-transition-contract](claim-transition-contract.md) | Claim lifecycle contract and the staged command adapters |
| [store](store.md) | The `ClaimStore` and its pyturso, libsql and sqlite adapters |
| [recall](recall.md) | `recall`, `briefing`, handles and the remote client |
| [mcp](mcp.md) | `kypp serve`, the agent-facing MCP surface |
| [hosted-worker](hosted-worker.md) | The Cloudflare Worker over D1: hosted memory, OAuth, System One |
| [portable-cooperation](portable-cooperation.md) | Handoffs and evidence references shared by Python and the Worker |
| [capture](capture.md) | `kypp capture` and `kypp sweep` over §0 session logs |
| [distill](distill.md) | Turning a §0 trace into candidate claims |
| [consolidate](consolidate.md) | Subject and semantic dedup |
| [verify](verify.md) | Re-running each claim's `verify` command |
| [transcripts](transcripts.md) | `kypp seed` from Claude Code and Codex transcripts |
| [eval-mining](eval-mining.md) | `kypp mine-tasks`: eval-task candidates from transcripts |
| [cli](cli.md) | The `kypp` command dispatcher |
