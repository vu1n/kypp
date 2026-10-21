// score.ts — run recall's System One rerank (s1.relevance, the exact request recall sends) over a
// labeled query set and save one row per (query, pooled claim): the keyword rank, the label and the
// relevance score. A relevant claim the keyword search never pooled has no row; report.py counts it. The claims are the write gate eval's labeled lessons, stored in an in-memory D1 so
// the pool comes from the real keyword search (store.recall). Responses are cached by request hash,
// so a rerun without --live reproduces the scores offline and the floor can be re-tuned with no model
// calls (scripts/tune_cutoff.py, then bench/recall_rerank/report.py for ranking quality).
//
//   node --experimental-strip-types bench/recall_rerank/score.ts \
//     --claims ../eval/write_gate/corpus.jsonl --queries ../eval/recall_rerank/queries.jsonl \
//     --cache ../eval/recall_rerank/cache.json --out ../eval/recall_rerank/scores.jsonl [--model clef] [--live]
//
// --chunk sets how many claims go in one request (recall's RERANK_CHUNK by default); --compare 1,40
// also scores the pool at those chunk sizes, saved as p_chunk1, p_chunk40. --live calls Workers AI over REST (CLOUDFLARE_ACCOUNT_ID,
// CLOUDFLARE_AI_TOKEN) on cache misses.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { RERANK_CHUNK, RERANK_POOL, type S1Client, relevance } from "../../src/s1.ts";
import { D1Store } from "../../src/store.ts";
import { memoryD1 } from "../../test/d1.ts";

const { values: o } = parseArgs({ options: {
  claims: { type: "string" }, queries: { type: "string" }, cache: { type: "string" }, out: { type: "string" },
  model: { type: "string", default: "clef" }, live: { type: "boolean", default: false },
  chunk: { type: "string", default: String(RERANK_CHUNK) }, compare: { type: "string", default: "" },
} });
if (!o.claims || !o.queries || !o.cache || !o.out) throw new Error("--claims, --queries, --cache and --out are required");

const cache: Record<string, unknown> = existsSync(o.cache) ? JSON.parse(readFileSync(o.cache, "utf8")) : {};
let misses = 0;

async function post(body: unknown): Promise<unknown> {
  const url = `https://api.cloudflare.com/client/v4/accounts/${process.env.CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/cloudflare/${o.model}`;
  for (let attempt = 0; ; attempt++) {
    const r = await fetch(url, { method: "POST", body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${process.env.CLOUDFLARE_AI_TOKEN}`, "Content-Type": "application/json" } });
    const env: any = await r.json().catch(() => ({}));
    if (r.ok && env.success !== false) return env.result;
    if (attempt >= 3) throw new Error(`Workers AI ${r.status}: ${JSON.stringify(env.errors ?? env).slice(0, 300)}`);
    await new Promise((res) => setTimeout(res, 2000 * 2 ** attempt));
  }
}

// Same shape as s1Client's AI-binding client; the cache key covers the whole request.
const client: S1Client = {
  async systemOne({ state, questions }) {
    const body = { model: o.model, state, questions };
    const key = createHash("sha256").update(JSON.stringify(body)).digest("hex");
    if (!(key in cache)) {
      if (!o.live) { misses++; throw new Error("cache miss (rerun with --live)"); }
      cache[key] = await post(body);
      writeFileSync(o.cache!, JSON.stringify(cache, null, 0));
    }
    return cache[key] as any;
  },
};

const lessons = readFileSync(o.claims, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)).filter((r) => r.label === "keep");
const compare = o.compare ? o.compare.split(",").map(Number) : [];
const queries = readFileSync(o.queries, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));

// Every lesson is a global, accepted claim, so visibility never hides one and the pool is the keyword
// search alone.
// Ids and clocks are fixed: bm25 ties fall back to updated_at and then id, and a random id or a real
// clock would change the pool's order, and so the cached request, on every run.
const db = memoryD1(), store = new D1Store(db);
const byId = new Map<string, string>(); // store id -> corpus id
const FIXED = "2026-10-21T00:00:00.000Z";
for (const r of lessons) {
  const tmp = await store.claim({ type: r.kind, subject: r.subject, content: r.content, scope: "global", project: null,
    confidence: 0.7, sourceIds: [], codeRefs: [], accept: true, agent: "eval", user: null });
  const id = createHash("sha256").update(r.id).digest("hex").slice(0, 32);
  await db.prepare("UPDATE memory_claims SET created_at = ?, updated_at = ? WHERE id = ?").bind(FIXED, FIXED, tmp).run();
  for (const sql of ["UPDATE memory_claims SET id = ? WHERE id = ?", "UPDATE claims_fts SET claim_id = ? WHERE claim_id = ?",
    "UPDATE code_pointers SET claim_id = ? WHERE claim_id = ?"]) await db.prepare(sql).bind(id, tmp).run();
  byId.set(id, r.id);
}

const out: string[] = [];
for (const q of queries) {
  // Pointer hits skip the model in recall (s1.rankForQuery), so they aren't scored here either.
  const pool = (await store.recall(q.q, null, null, { candidates: "none", limit: RERANK_POOL })).filter((c) => !c.pointer);
  const scored: Record<string, number[]> = {};
  for (const chunk of [Number(o.chunk), ...compare]) {
    const s = await relevance(client, q.q, pool, 60_000, chunk);
    if (pool.length && !s) throw new Error(`no scores for ${q.id} at chunk ${chunk}${misses ? " (cache miss)" : ""}`);
    scored[chunk === Number(o.chunk) ? "p_rel" : `p_chunk${chunk}`] = s ?? [];
  }
  pool.forEach((c, rank) => {
    const cid = byId.get(c.id)!;
    out.push(JSON.stringify({ query: q.id, kind: q.kind, claim: cid, label: q.relevant.includes(cid) ? "relevant" : "irrelevant",
      fts_rank: rank, model: o.model, chunk: Number(o.chunk), ...Object.fromEntries(Object.entries(scored).map(([k, v]) => [k, v[rank]])) }));
  });
}
writeFileSync(o.out, out.join("\n") + "\n");
console.log(`scored ${out.length} rows for ${queries.length} queries on ${o.model}`);
