// Pure port of kypp/claim_machine.py. Trusted in-process commands, not an HTTP/MCP dialect.
// Context: doc://kypp/authority-order@0008#authority-dominates — authority bounds replacement; a blocked check records evidence without granting privilege.
// Context: doc://kypp/append-only-history@0002#never-delete — rejected/superseded assertions never revive and history is retained.
// Context: doc://kypp/verify-operator-only@0003#operator-only-verify — shell configuration and check tokens are operator capabilities, never client-selected roles.
import { AUTHORITIES, type Authority } from "./memory.ts";
export interface Actor { principal: string; role: "agent" | "operator" | "maintenance" }
export interface Stamp { source?: string | null; echo?: boolean }
export interface Token { claim_id: string; claim_revision: number; attempt: number; verifier_digest: string; head_revision: number }
export interface Evidence { claim_id: string; claim_revision: number; principal: string; source: string }
export interface MachineClaim {
  id: string; content: string; principal: string; source: string | null; echo: boolean; based_on: number;
  previous: string | null; status: "candidate" | "accepted" | "rejected" | "superseded";
  authority: Authority; acceptance: string | null; revision: number;
  successor: string | null; verifier: string | null; attempt: number; pending_check: Token | null;
  check: "passed" | "failed" | "inconclusive" | null;
}
export type Action =
  | { kind: "Propose"; claim_id: string; content: string; previous?: string | null; verifier?: string | null }
  | { kind: "Correct"; claim_id: string; content: string; verifier?: string | null }
  | { kind: "Accept"; claim_id: string; reason?: string; support?: string[]; agreement?: string }
  | { kind: "Reject"; claim_id: string; reason?: string | null }
  | { kind: "BeginCheck"; claim_id: string }
  | { kind: "CompleteCheck"; token: Token; outcome: "passed" | "failed" | "inconclusive" };
export interface Command { id: string; expected_revision: number; action: Action }
export interface Receipt { principal: string; command_id: string; fingerprint: string; revision: number; claim_id: string; token: Token | null }
export interface Event {
  revision: number; command_id: string; principal: string; kind: string; claim_id: string;
  evidence: Evidence[]; reason: string | null; check_token: Token | null; check_outcome: string | null;
}
export interface State { revision: number; head: string | null; head_revision: number; claims: MachineClaim[]; receipts: Receipt[]; events: Event[] }
export class Refused extends Error {
  code: string;
  constructor(code: string) { super(code); this.code = code; }
}
export function requireThat(condition: unknown, code: string): asserts condition {
  if (!condition) throw new Refused(code);
}
export const emptyState = (): State => ({ revision: 0, head: null, head_revision: 0, claims: [], receipts: [], events: [] });
const AUTHORITY = Object.fromEntries(AUTHORITIES.map((tier, rank) => [tier, rank])) as Record<Authority, number>;
// Python str.split/strip whitespace, including NEL and the C0 separators but not BOM.
// Keep exact-content agreement byte-for-byte portable rather than relying on JS \s.
const SPACE = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/u;
export const normalizedContent = (value: string): string => value.split(SPACE).filter(Boolean).join(" ");
const live = (c: MachineClaim) => requireThat(!["rejected", "superseded"].includes(c.status), "retired");
export const usable = (c: MachineClaim) => c.status === "accepted" && (c.verifier === null || c.check === "passed");
export function get(state: State, id: string): MachineClaim {
  const claim = state.claims.find((c) => c.id === id);
  requireThat(claim, "not_found");
  return claim;
}
export function encoded(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(encoded).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([k, v]) => `${JSON.stringify(k)}:${encoded(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
async function digest(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), (n) => n.toString(16).padStart(2, "0")).join("");
}
function normalized(a: Action): object {
  switch (a.kind) {
    case "Propose": return { claim_id: a.claim_id, content: a.content, previous: a.previous ?? null, verifier: a.verifier ?? null };
    case "Correct": return { claim_id: a.claim_id, content: a.content, verifier: a.verifier ?? null };
    case "Accept": return { claim_id: a.claim_id, reason: a.reason ?? "explicit", support: a.support ?? [], agreement: a.agreement ?? "unknown" };
    case "Reject": return { claim_id: a.claim_id, reason: a.reason ?? null };
    case "BeginCheck": return { claim_id: a.claim_id };
    case "CompleteCheck": return { token: a.token, outcome: a.outcome };
  }
}
export const fingerprint = (actor: Actor, command: Command): Promise<string> => digest(encoded({
  role: actor.role, action: command.action.kind, command: { ...command, action: normalized(command.action) },
}));
function newClaim(id: string, content: string, principal: string): MachineClaim {
  return { id, content, principal, source: null, echo: false, based_on: 0, previous: null, status: "candidate",
    authority: "agent", acceptance: null, revision: 1, successor: null, verifier: null, attempt: 0, pending_check: null, check: null };
}
function put(state: State, claim: MachineClaim) {
  const i = state.claims.findIndex((c) => c.id === claim.id);
  state.claims[i] = claim;
}
function insert(state: State, claim: MachineClaim) {
  requireThat(claim.id && normalizedContent(claim.content), "invalid");
  requireThat(!state.claims.some((c) => c.id === claim.id), "id_exists");
  if (claim.previous !== null) get(state, claim.previous);
  state.claims.push(claim);
}
function accept(state: State, claim: MachineClaim, reason: string, support: string[] = []) {
  requireThat(claim.status === "candidate", "not_candidate");
  const retire = (other: MachineClaim) => put(state, { ...other, status: "superseded", successor: claim.id, revision: other.revision + 1 });
  if (state.head !== null) {
    const head = get(state, state.head);
    requireThat(AUTHORITY[claim.authority] >= AUTHORITY[head.authority], "lower_authority");
    retire(head);
  }
  for (const id of support) if (id !== claim.id) retire(get(state, id));
  put(state, { ...claim, status: "accepted", acceptance: reason, revision: claim.revision + 1 });
  state.head = claim.id;
  state.head_revision = state.revision + 1;
}

// No input mutation, shell or model call. The DB adapter commits the returned state atomically.
export async function transition(before: State, actor: Actor, command: Command, stamp?: Stamp, k = 2): Promise<[State, Receipt]> {
  requireThat(["agent", "operator", "maintenance"].includes(actor.role) && actor.principal, "forbidden");
  requireThat(command.id && k >= 2, "invalid");
  const fp = await fingerprint(actor, command);
  const replay = before.receipts.find((r) => r.principal === actor.principal && r.command_id === command.id);
  if (replay) { requireThat(replay.fingerprint === fp, "idempotency_conflict"); return [before, replay]; }
  requireThat(command.expected_revision === before.revision, "stale");
  const state = structuredClone(before), a = command.action;
  let claim: MachineClaim, token: Token | null = null, evidence: Evidence[] = [];
  switch (a.kind) {
    case "Propose": {
      requireThat(actor.role === "agent" || actor.role === "operator", "forbidden");
      requireThat(a.verifier == null || (actor.role === "operator" && normalizedContent(a.verifier)), "forbidden");
      requireThat(stamp !== undefined, "missing_stamp");
      const source = stamp.source ?? null;
      const prior = state.claims.filter((c) => source !== null && c.source === source);
      claim = { ...newClaim(a.claim_id, a.content, actor.principal), source,
        echo: !!stamp.echo || prior.some((c) => c.echo), based_on: prior.length ? Math.min(...prior.map((c) => c.based_on)) : state.head_revision,
        previous: a.previous ?? null, verifier: a.verifier ?? null };
      insert(state, claim);
      break;
    }
    case "Correct": {
      requireThat(actor.role === "operator", "forbidden");
      requireThat(a.verifier == null || normalizedContent(a.verifier), "invalid");
      claim = { ...newClaim(a.claim_id, a.content, actor.principal), authority: "human", based_on: state.head_revision,
        previous: state.head, verifier: a.verifier ?? null };
      const replaced = state.claims.filter((c) => !["rejected", "superseded"].includes(c.status) && c.id !== state.head).map((c) => c.id);
      insert(state, claim);
      accept(state, claim, "correction", replaced);
      break;
    }
    case "Accept": {
      claim = get(state, a.claim_id); live(claim);
      const reason = a.reason ?? "explicit", support = a.support ?? [];
      if (reason === "explicit") {
        requireThat(actor.role === "operator", "forbidden");
        requireThat(!support.length, "invalid");
      } else if (reason === "recurrence") {
        requireThat(actor.role === "maintenance", "forbidden");
        requireThat(new Set(support).size === support.length && support.includes(claim.id), "invalid_support");
        const members = support.map((id) => get(state, id));
        requireThat(members.every((c) => c.status === "candidate" && c.authority === "agent" && !c.echo && c.source), "invalid_support");
        requireThat(members.some((c) => c.based_on === state.head_revision), "fresh_support_required");
        requireThat(new Set(members.map((c) => c.source)).size >= k, "insufficient_recurrence");
        requireThat(a.agreement === "exact" || a.agreement === "agrees", "agreement_required");
        if (a.agreement === "exact") requireThat(new Set(members.map((c) => normalizedContent(c.content))).size === 1, "agreement_required");
        evidence = members.map((c) => ({ claim_id: c.id, claim_revision: c.revision, principal: c.principal, source: c.source! }));
      } else throw new Refused("invalid");
      accept(state, claim, reason, support);
      break;
    }
    case "Reject": {
      claim = get(state, a.claim_id);
      requireThat(actor.role === "operator" || (actor.role === "maintenance" && claim.status === "candidate" && claim.authority === "agent"), "forbidden");
      live(claim);
      put(state, { ...claim, status: "rejected", revision: claim.revision + 1 });
      if (state.head === claim.id) { state.head = null; state.head_revision = state.revision + 1; }
      break;
    }
    case "BeginCheck": {
      requireThat(actor.role === "operator", "forbidden");
      claim = get(state, a.claim_id); live(claim);
      requireThat(claim.verifier !== null, "no_verifier");
      token = { claim_id: claim.id, claim_revision: claim.revision + 1, attempt: claim.attempt + 1,
        verifier_digest: await digest(claim.verifier), head_revision: state.head_revision };
      put(state, { ...claim, revision: token.claim_revision, attempt: token.attempt, pending_check: token });
      break;
    }
    case "CompleteCheck": {
      requireThat(actor.role === "operator", "forbidden");
      claim = get(state, a.token.claim_id); live(claim);
      requireThat(claim.verifier !== null, "no_verifier");
      requireThat(encoded(a.token) === encoded(claim.pending_check) && a.token.claim_revision === claim.revision
        && a.token.verifier_digest === await digest(claim.verifier), "stale_attempt");
      requireThat(["passed", "failed", "inconclusive"].includes(a.outcome), "invalid");
      let checked = { ...claim, check: a.outcome, revision: claim.revision + 1, pending_check: null };
      const tier = claim.authority === "human" ? "human" : "verified";
      const replaceable = a.token.head_revision === state.head_revision
        && (state.head === null || AUTHORITY[tier] >= AUTHORITY[get(state, state.head).authority]);
      if (a.outcome === "passed" && checked.status === "candidate" && replaceable) accept(state, { ...checked, authority: tier }, "verification");
      else {
        if (a.outcome === "passed" && checked.status === "accepted") checked = { ...checked, authority: tier };
        put(state, checked);
      }
      break;
    }
    default: throw new Refused("invalid");
  }
  const revision = state.revision + 1;
  const receipt: Receipt = { principal: actor.principal, command_id: command.id, fingerprint: fp, revision, claim_id: claim.id, token };
  state.revision = revision;
  state.receipts.push(receipt);
  state.events.push({ revision, command_id: command.id, principal: actor.principal, kind: a.kind, claim_id: claim.id, evidence,
    reason: a.kind === "Accept" ? a.reason ?? "explicit" : a.kind === "Reject" ? a.reason ?? null : null,
    check_token: a.kind === "CompleteCheck" ? a.token : token, check_outcome: a.kind === "CompleteCheck" ? a.outcome : null });
  return [state, receipt];
}
