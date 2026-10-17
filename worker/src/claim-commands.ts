// Staged command persistence, isolated from memory_claims. No public route calls it yet.
import { type Actor, type Command, type Receipt, type Stamp, type State, Refused,
  emptyState, encoded, fingerprint, normalizedContent, requireThat, transition } from "./claim-machine.ts";

export interface Group { subject: string; scope: "project" | "group" | "global" | "user" | "room"; project?: string | null; owner?: string | null }
export function groupKey(group: Group): string {
  const project = group.project ?? null, owner = group.owner ?? null;
  requireThat(normalizedContent(group.subject) && ["project", "group", "global", "user", "room"].includes(group.scope), "invalid_group");
  requireThat((owner !== null) === ["user", "room"].includes(group.scope), "invalid_group");
  requireThat(owner === null || normalizedContent(owner), "invalid_group");
  requireThat(["project", "group"].includes(group.scope) ? !!project : project === null, "invalid_group");
  return encoded([group.subject, group.scope, project, owner]);
}

export interface ClaimCommands {
  read(group: Group): Promise<State>;
  execute(group: Group, actor: Actor, command: Command, stamp?: Stamp, k?: number): Promise<Receipt>;
}

// Context: doc://kypp/memory-scope-decay@0011#scope-keys-decay — the revision guard and all aggregate history commit in one D1 batch.
export class D1ClaimCommands implements ClaimCommands {
  private db: D1Database;
  constructor(db: D1Database) { this.db = db; }

  async read(group: Group): Promise<State> {
    const row = await this.db.prepare("SELECT state FROM claim_command_groups WHERE group_key = ?").bind(groupKey(group)).first<{ state: string }>();
    return row ? JSON.parse(row.state) : emptyState();
  }

  private async replay(key: string, actor: Actor, command: Command): Promise<Receipt | null> {
    const row = await this.db.prepare(
      "SELECT group_key,fingerprint,receipt FROM claim_command_receipts WHERE principal = ? AND command_id = ?",
    ).bind(actor.principal, command.id).first<{ group_key: string; fingerprint: string; receipt: string }>();
    if (!row) return null;
    requireThat(row.group_key === key && row.fingerprint === await fingerprint(actor, command), "idempotency_conflict");
    return JSON.parse(row.receipt);
  }

  async execute(group: Group, actor: Actor, command: Command, stamp?: Stamp, k = 2): Promise<Receipt> {
    const key = groupKey(group), replay = await this.replay(key, actor, command);
    if (replay) return replay;
    const before = await this.read(group);
    const [after, receipt] = await transition(before, actor, command, stamp, k);
    const stmts = [
      this.db.prepare("INSERT INTO claim_command_guard(ok) SELECT 0 WHERE COALESCE((SELECT revision FROM claim_command_groups WHERE group_key = ?), 0) != ?")
        .bind(key, before.revision),
      this.db.prepare("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?) ON CONFLICT(group_key) DO UPDATE SET revision=excluded.revision,state=excluded.state")
        .bind(key, after.revision, encoded(after)),
      ...after.claims.filter((c) => !before.claims.some((old) => old.id === c.id)).map((c) =>
        this.db.prepare("INSERT INTO claim_command_ids(claim_id,group_key) VALUES(?,?)").bind(c.id, key)),
      this.db.prepare("INSERT INTO claim_command_receipts(principal,command_id,group_key,fingerprint,receipt) VALUES(?,?,?,?,?)")
        .bind(actor.principal, command.id, key, receipt.fingerprint, encoded(receipt)),
    ];
    try { await this.db.batch(stmts); }
    catch (error) {
      const replay = await this.replay(key, actor, command);
      if (replay) return replay;
      const message = String((error as Error).message);
      if (message.includes("claim_command_cas")) throw new Refused("stale");
      if (message.includes("UNIQUE constraint failed: claim_command_ids.claim_id")) throw new Refused("id_exists");
      throw error;
    }
    return receipt;
  }
}
