// Runs replay.ts in plain Node against test/d1.ts (node:sqlite), a third SQLite build, for comparison.
// usage: node --experimental-strip-types bench/replay/node_replay.ts <corpus.jsonl> <queries.json> <out.json>
import { readFileSync, writeFileSync } from "node:fs";
import { memoryD1 } from "../../test/d1.ts";
import replay from "./replay.ts";

const [corpus, queries, out] = process.argv.slice(2);
const env = { DB: memoryD1() };
const call = async (path: string, body: unknown) =>
  (await replay.fetch(new Request(`http://x${path}`, { method: "POST", body: JSON.stringify(body) }), env)).json();
const rows = readFileSync(corpus, "utf8").trim().split("\n").map((l) => JSON.parse(l));
for (let i = 0; i < rows.length; i += 250) await call("/seed", { claims: rows.slice(i, i + 250) });
const res: any[] = [];
for (const mode of ["none", "origin"]) for (const q of JSON.parse(readFileSync(queries, "utf8"))) {
  const t0 = performance.now();
  const r: any = await call("/recall", { ...q, candidates: mode });
  res.push({ ...q, candidates: mode, ids: r.ids, client_ms: performance.now() - t0 });
}
writeFileSync(out, JSON.stringify({ queries: res }));
console.error("node:sqlite", process.versions.sqlite, "done", res.length);
