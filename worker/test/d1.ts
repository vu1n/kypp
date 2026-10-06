// A minimal D1 stand-in over node:sqlite, so the store's SQL runs in plain node tests. batch() is a
// transaction like D1's: any failing statement rolls the whole batch back.
import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

class Stmt {
  db: DatabaseSync; sql: string; args: unknown[];
  constructor(db: DatabaseSync, sql: string, args: unknown[] = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args: unknown[]) { return new Stmt(this.db, this.sql, args); }
  async all() { return { results: this.db.prepare(this.sql).all(...(this.args as any[])) as Record<string, unknown>[] }; }
  async first() { return (await this.all()).results[0] ?? null; }
  async run() { this.db.prepare(this.sql).run(...(this.args as any[])); return { success: true }; }
}

export function memoryD1(): D1Database {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(new URL("../migrations", import.meta.url)).sort()) {
    db.exec(readFileSync(new URL(`../migrations/${f}`, import.meta.url), "utf8"));
  }
  return {
    prepare: (sql: string) => new Stmt(db, sql),
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
  } as unknown as D1Database;
}
