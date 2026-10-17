// A minimal D1 stand-in over node:sqlite, so the store's SQL runs in plain node tests. batch() is a
// transaction like D1's: any failing statement rolls the whole batch back.
import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

class Stmt {
  db: DatabaseSync; sql: string; args: unknown[]; maxBytes: number;
  constructor(db: DatabaseSync, sql: string, maxBytes: number, args: unknown[] = []) { this.db = db; this.sql = sql; this.args = args; this.maxBytes = maxBytes; }
  bind(...args: unknown[]) { return new Stmt(this.db, this.sql, this.maxBytes, args); }
  check(values: unknown[], row = false) {
    const lengths = values.map((v) => typeof v === "string" ? Buffer.byteLength(v, "utf8")
      : ArrayBuffer.isView(v) ? v.byteLength : v instanceof ArrayBuffer ? v.byteLength : 0);
    if (lengths.some((n) => n > this.maxBytes) || (row && lengths.reduce((a, b) => a + b, 0) > this.maxBytes)) {
      throw new Error("D1 size limit (test harness)");
    }
  }
  async all() {
    this.check(this.args);
    const results = this.db.prepare(this.sql).all(...(this.args as any[])) as Record<string, unknown>[];
    for (const row of results) this.check(Object.values(row), true);
    return { results };
  }
  async first() { return (await this.all()).results[0] ?? null; }
  async run() { this.check(this.args); const r = this.db.prepare(this.sql).run(...(this.args as any[])); return { success: true, meta: { changes: Number(r.changes) } }; }
}

// This models D1's 2,000,000-byte value/row ceiling for command storage. It is
// not an emulator of all D1 query, request, timing or replication limits.
export function memoryD1(opts: { maxBytes?: number; throughMigration?: number } = {}): D1Database & { applyRemainingMigrations(): void } {
  const db = new DatabaseSync(":memory:");
  const maxBytes = opts.maxBytes ?? 2_000_000, applied = new Set<string>();
  function migrate(through = Infinity) {
    for (const f of readdirSync(new URL("../migrations", import.meta.url)).sort()) {
      if (applied.has(f) || Number(f.slice(0, 4)) > through) continue;
      db.exec("BEGIN");
      try {
        db.exec(readFileSync(new URL(`../migrations/${f}`, import.meta.url), "utf8"));
        db.exec("COMMIT");
        applied.add(f);
      } catch (error) { db.exec("ROLLBACK"); throw error; }
    }
    for (const table of ["claim_command_groups", "claim_command_ids", "claim_command_events", "claim_command_receipts"]) {
      const cols = db.prepare(`PRAGMA table_info(${table})`).all() as { name: string }[];
      if (!cols.length) continue;
      const size = cols.map((c) => `COALESCE(length(CAST(NEW.${c.name} AS BLOB)),0)`).join("+");
      for (const op of ["INSERT", "UPDATE"]) db.exec(
        `CREATE TRIGGER IF NOT EXISTS size_${table}_${cols.length}_${op} BEFORE ${op} ON ${table}`
        + ` WHEN (${size}) > ${maxBytes} BEGIN SELECT RAISE(ABORT,'D1 row size limit (test harness)'); END`,
      );
    }
  }
  migrate(opts.throughMigration);
  return {
    applyRemainingMigrations: () => migrate(),
    prepare: (sql: string) => new Stmt(db, sql, maxBytes),
    async batch(stmts: Stmt[]) {
      db.exec("BEGIN");
      try {
        const out = [];
        for (const s of stmts) out.push(await s.run());
        db.exec("COMMIT");
        return out;
      } catch (e) {
        db.exec("ROLLBACK");
        throw e;
      }
    },
  } as unknown as D1Database & { applyRemainingMigrations(): void };
}
