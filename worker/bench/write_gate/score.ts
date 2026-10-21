// score.ts — run the live write gate (s1.judge, the exact request the claim tool sends) over a labeled
// lesson set and save one row per item: id, label, kind, model, p_keep, p_control. Responses are cached
// by request hash, so a rerun without --live reproduces the scores offline and the cutoff can be
// re-tuned with no model calls (scripts/tune_cutoff.py).
//
//   node --experimental-strip-types bench/write_gate/score.ts \
//     --corpus ../eval/write_gate/corpus.jsonl --cache ../eval/write_gate/cache.json \
//     --out ../eval/write_gate/scores.jsonl [--model clef] [--live]
//
// --live calls Workers AI over REST (CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_AI_TOKEN) on cache misses.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { type S1Client, judge } from "../../src/s1.ts";

const { values: o } = parseArgs({ options: {
  corpus: { type: "string" }, cache: { type: "string" }, out: { type: "string" },
  model: { type: "string", default: "clef" }, live: { type: "boolean", default: false },
} });
if (!o.corpus || !o.cache || !o.out) throw new Error("--corpus, --cache and --out are required");

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

const rows = readFileSync(o.corpus, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const out: string[] = [];
for (const r of rows) {
  // A generous deadline: this measures the model, not Workers AI latency.
  const v = await judge(client, { subject: r.subject, content: r.content, project: r.project }, 60_000);
  if (v.keep === null) throw new Error(`no keep score for ${r.id}${misses ? " (cache miss)" : ""}`);
  out.push(JSON.stringify({ id: r.id, label: r.label, kind: r.kind, model: o.model, p_keep: v.keep, p_control: v.control }));
}
writeFileSync(o.out, out.join("\n") + "\n");
console.log(`scored ${out.length} items on ${o.model}`);
