// memory.ts — the governance rules, ported from kypp/arbiter.py and kypp/view.py. Pure functions
// over Claim rows (no D1), so the node tests exercise exactly what the Worker runs. Keep these in
// step with the Python originals: local and cloud stores must rank and promote the same way.

export const TYPES = ["fact", "preference", "decision", "procedure", "artifact", "hypothesis", "pitfall"] as const;
export const AUTHORITIES = ["agent", "verified", "human"] as const;
export type ClaimType = (typeof TYPES)[number];
export type Authority = (typeof AUTHORITIES)[number];
// Scopes: this repo, the signed-in user across repos, everyone. The agent is a label, not a scope.
export const SCOPES = ["project", "user", "global"] as const;
export type Scope = (typeof SCOPES)[number];

// Same value as vocab.HUMAN_CORRECTION_CONFIDENCE.
export const HUMAN_CORRECTION_CONFIDENCE = 0.95;

export interface Claim {
  id: string;
  type: ClaimType;
  subject: string;
  content: string;
  scope: Scope;
  project: string | null;
  agent: string | null;
  user: string | null;
  status: "candidate" | "accepted" | "superseded" | "rejected";
  authority: Authority;
  confidence: number;
  source_ids: string[];
  code_refs: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
}

// Context: doc://kypp/authority-order@0003#authority-dominates — rank survivors by authority first; agent < verified < human.
export function rank(c: Claim): [number, number, number, number, string] {
  return [AUTHORITIES.indexOf(c.authority), c.status === "accepted" ? 1 : 0, c.confidence ?? 0,
    c.source_ids.length, c.updated_at];
}

function compareRank(a: Claim, b: Claim): number {
  const ra = rank(a), rb = rank(b);
  for (let i = 0; i < ra.length; i++) {
    if (ra[i] < rb[i]) return -1;
    if (ra[i] > rb[i]) return 1;
  }
  return 0;
}

export function survivor(members: Claim[]): Claim {
  return members.reduce((best, c) => (compareRank(c, best) > 0 ? c : best));
}

// Distinct sessions/observations backing a subject (arbiter._corroboration).
export function corroboration(members: Claim[]): number {
  return new Set(members.flatMap((m) => m.source_ids)).size;
}

export interface Plan {
  supersede: string[];
  promote: string[];
}

// arbiter.consolidate, exact-subject phases only: group live claims by (subject, scope, project, and
// owner for the user scope),
// keep the strongest and supersede the rest; promote a candidate survivor once >= k claims from
// >= k distinct sources agree.
export function planConsolidation(live: Claim[], k = 2): Plan {
  const groups = new Map<string, Claim[]>();
  for (const c of live) {
    const key = JSON.stringify([c.subject, c.scope, c.project, c.scope === "user" ? c.user : null]);
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  const plan: Plan = { supersede: [], promote: [] };
  for (const members of groups.values()) {
    const best = survivor(members);
    if (members.length > 1) plan.supersede.push(...members.filter((m) => m.id !== best.id).map((m) => m.id));
    // Why: accept only when >= K distinct claims from distinct sessions agree; one session's guess must not become the swarm's truth.
    if (best.status === "candidate" && members.length >= k && corroboration(members) >= k) plan.promote.push(best.id);
  }
  return plan;
}

// view.compact_line, minus code grounding (the Worker has no checkout to resolve anchors against;
// it names the first anchored path instead).
const CLIP = 240;
export function compactLine(c: Claim): string {
  const mark = c.status === "accepted" ? "✓" : "?";
  const auth = c.authority === "human" ? " 👤" : c.authority === "verified" ? " ☑" : "";
  const body = c.content.split(/\s+/).filter(Boolean).join(" ");
  let line = `${c.id.slice(0, 8)} [${c.type} ${mark}${c.confidence.toFixed(1)}${auth}] ${c.subject} — ${body.slice(0, CLIP)}`;
  if (body.length > CLIP) line += `… (expand ${c.id.slice(0, 8)} for full)`;
  const path = c.code_refs.find((r) => typeof r.path === "string")?.path;
  if (path) line += ` → ${path}`;
  return line;
}

export function renderClaims(claims: Claim[], empty = "(no matching memory)"): string {
  return claims.map(compactLine).join("\n") || empty;
}

// The briefing, one heading per type in briefingOrder's order.
export function renderBriefing(claims: Claim[]): string {
  if (!claims.length) return "(no accepted memory yet)";
  const out: string[] = [];
  for (const c of claims) {
    const head = `# ${c.type}`;
    if (!out.includes(head)) out.push(head);
    out.push(compactLine(c));
  }
  return out.join("\n");
}

// view.briefing_claims ordering: traps first, then choices made, then how-tos. Stable sort keeps
// the store's strength order within a type.
const BRIEFING_PRIORITY: Record<string, number> = { pitfall: 0, decision: 1, procedure: 2 };
export function briefingOrder(claims: Claim[], limit: number): Claim[] {
  return [...claims].sort((a, b) => (BRIEFING_PRIORITY[a.type] ?? 3) - (BRIEFING_PRIORITY[b.type] ?? 3)).slice(0, limit);
}

// An FTS5 MATCH expression from free text: each word quoted (so FTS syntax in the query can't
// break the parse), OR-joined like the local keyword recall. Empty when the query has no words.
export function ftsQuery(text: string): string {
  return (text.match(/[A-Za-z0-9_]+/g) ?? []).map((t) => `"${t}"`).join(" OR ");
}
