# Deferred: TurboQuant / turbovec for recall vector search

**Status:** evaluated 2026-06-07, deferred. Revisit at ~50k+ claims or when recall latency is measurably a problem.

## What was evaluated

- **Paper:** [TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate](https://arxiv.org/abs/2504.19874) (Zandieh, Daliri, Hadian, Mirrokni — Google Research). Random rotation → coordinates concentrate to a Beta distribution → optimal scalar (Lloyd-Max) quantization per coordinate; 1-bit QJL pass on residuals makes inner-product estimates unbiased. Near-optimal distortion at every bit-width (within ~2.7× of the information-theoretic bound). Key property: **data-oblivious / online** — no codebook training phase, unlike the PQ/IVF-PQ family.
- **Implementation:** [RyanCodrai/turbovec](https://github.com/RyanCodrai/turbovec) — Rust core + Python bindings, NEON/AVX-512 kernels, `TurboQuantIndex(dim, bit_width)`, filtered search, `IdMapIndex` (external IDs survive deletions), binary persistence. Active and credible (~5.9k stars at evaluation time); benchmark claims not independently verified.

## Why deferred

The problem it solves appears at 10⁵–10⁷ vectors (index RAM, PQ training time). At evaluation time the corpus was **~275 claims** (<1MB of float32 at 768 dims). The tursodb linear scan (`store.py`, "linear scan — fine at bootstrap") is microseconds at this size and holds to ~10⁴–10⁵ claims.

Integrating now would cost without paying:

- **Dual source of truth.** Embeddings live in the claims table; a sidecar index needs sync on every capture and every arbiter merge/delete. `IdMapIndex` mitigates but doesn't eliminate the consistency surface.
- **Doesn't touch actual bottlenecks.** Current pain is write contention (MVCC retry loop) and per-claim `rg` subprocess at recall time — quantized search helps neither.

## Escalation path when recall gets slow

1. **~10⁴ claims:** do nothing — linear scan holds.
2. **~10⁵ claims:** Turso/libSQL native DiskANN vector index first — zero new dependencies, stays in-DB.
3. **Genuinely large multi-repo swarm corpus:** turbovec — its no-training online ingestion matches kypp's write pattern (claims trickle in continuously; batch-retrained PQ is the wrong shape).

Side note, independent of any library: 2–4-bit quantization of the embedding BLOBs in the claims table would shrink them ~8–16×. Also a non-problem at current scale.
