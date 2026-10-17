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
  // Trusted caller authenticates actor and exact group reach before every call, including retries.
  execute(group: Group, actor: Actor, command: Command, stamp?: Stamp, k?: number): Promise<Receipt>;
}

// Context: doc://kypp/memory-scope-decay@0011#scope-keys-decay — the revision guard and all aggregate history commit in one D1 batch.
export class D1ClaimCommands implements ClaimCommands {
  private db: D1Database;
  constructor(db: D1Database) { this.db = db; }

  async read(group: Group): Promise<State> {
    // Historical inspection/conformance only. execute() never loads event/receipt history.
    return this.load(group, true);
  }

  private async load(group: Group, history: boolean): Promise<State> {
    const key = groupKey(group);
    const header = () => this.db.prepare("SELECT revision,state FROM claim_command_groups WHERE group_key = ?")
      .bind(key).first<{ revision: number; state: string }>();
    for (let attempt = 0; attempt < 5; attempt++) {
      const before = await header();
      const metadata = before ? JSON.parse(before.state) : { storage_version: 2, head: null, head_revision: 0 };
      requireThat(metadata.storage_version === 2, "storage_upgrade_required");
      const rows = await this.db.prepare("SELECT claim FROM claim_command_ids WHERE group_key = ? ORDER BY ordinal")
        .bind(key).all<{ claim: string | null }>();
      requireThat(rows.results.every((row) => row.claim !== null), "storage_inconsistent");
      const state: State = { ...emptyState(), revision: before?.revision ?? 0,
        head: metadata.head, head_revision: metadata.head_revision,
        claims: rows.results.map((row) => JSON.parse(row.claim!)) };
      if (history) {
        const events = await this.db.prepare("SELECT event FROM claim_command_events WHERE group_key = ? ORDER BY revision")
          .bind(key).all<{ event: string }>();
        const receipts = await this.db.prepare("SELECT receipt FROM claim_command_receipts WHERE group_key = ?")
          .bind(key).all<{ receipt: string }>();
        state.events = events.results.map((row) => JSON.parse(row.event));
        state.receipts = receipts.results.map((row) => JSON.parse(row.receipt) as Receipt).sort((a, b) => a.revision - b.revision);
      }
      const after = await header();
      if (before === null ? after === null : after?.revision === before.revision && after.state === before.state) return state;
    }
    throw new Refused("stale");
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
    // Match kernel preconditions before the durable receipt fast path.
    requireThat(["agent", "operator", "maintenance"].includes(actor.role) && actor.principal, "forbidden");
    requireThat(command.id && k >= 2, "invalid");
    const key = groupKey(group), replay = await this.replay(key, actor, command);
    if (replay) return replay;
    let before: State;
    try {
      before = await this.load(group, false);
    } catch (error) {
      // Retry exhaustion can race a commit, but storage refusals must propagate.
      if (error instanceof Refused && error.code === "stale") {
        const replay = await this.replay(key, actor, command);
        if (replay) return replay;
      }
      throw error;
    }
    let after: State, receipt: Receipt;
    try {
      [after, receipt] = await transition(before, actor, command, stamp, k);
    } catch (error) {
      // A concurrent receipt wins over stale planning or changed-input refusals.
      if (error instanceof Refused) {
        const replay = await this.replay(key, actor, command);
        if (replay) return replay;
      }
      throw error;
    }
    const stmts = [
      this.db.prepare("INSERT INTO claim_command_guard(ok) SELECT 0 WHERE COALESCE((SELECT revision FROM claim_command_groups WHERE group_key = ?), 0) != ?")
        .bind(key, before.revision),
      this.db.prepare("INSERT INTO claim_command_groups(group_key,revision,state) VALUES(?,?,?) ON CONFLICT(group_key) DO UPDATE SET revision=excluded.revision,state=excluded.state")
        .bind(key, after.revision, encoded({ storage_version: 2, head: after.head, head_revision: after.head_revision })),
    ];
    const prior = new Map(before.claims.map((c) => [c.id, c]));
    for (const claim of after.claims) {
      const old = prior.get(claim.id);
      if (!old) stmts.push(this.db.prepare("INSERT INTO claim_command_ids(claim_id,group_key,ordinal,claim) VALUES(?,?,?,?)")
        .bind(claim.id, key, after.revision, encoded(claim)));
      // The kernel increments the claim revision for every mutable projection change.
      else if (claim.revision !== old.revision) stmts.push(this.db.prepare("UPDATE claim_command_ids SET claim=? WHERE claim_id=? AND group_key=?")
        .bind(encoded(claim), claim.id, key));
    }
    stmts.push(
      this.db.prepare("INSERT INTO claim_command_events(group_key,revision,event) VALUES(?,?,?)")
        .bind(key, after.revision, encoded(after.events[after.events.length - 1])),
      this.db.prepare("INSERT INTO claim_command_receipts(principal,command_id,group_key,fingerprint,receipt) VALUES(?,?,?,?,?)")
        .bind(actor.principal, command.id, key, receipt.fingerprint, encoded(receipt)),
    );
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
