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

// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — an origin only files into a project the operator registered; an unknown name never creates one.
// An exact name wins; then a registered path the origin ends with ("https://github.com/vu1n/kypp.git"
// → "vu1n/kypp"); then the last path segment, so "vu1n/Kypp" resolves to "kypp". Each fallback
// counts only when it names exactly one project: two registered "foo"s leave the claim unsorted.
const path = (s: string) => s.trim().replace(/\.git$/i, "").toLowerCase().split("/").filter(Boolean);
const only = (xs: string[]) => (xs.length === 1 ? xs[0] : null);
export function resolveOrigin(origin: string | null | undefined, known: string[]): string | null {
  const given = (origin ?? "").trim(), segs = path(given);
  if (!segs.length) return null;
  if (known.includes(given)) return given;
  const suffix = only(known.filter((k) => {
    const ks = path(k);
    return ks.length > 1 && ks.length <= segs.length && ks.every((x, i) => x === segs[segs.length - ks.length + i]);
  }));
  return suffix ?? only(known.filter((k) => path(k).at(-1) === segs.at(-1)));
}

// Where a claim sits, as shown on every read line.
export function level(c: Pick<Claim, "scope" | "project">): string {
  return c.scope === "project" ? c.project ?? "unsorted" : c.scope;
}

// Same value as vocab.HUMAN_CORRECTION_CONFIDENCE.
export const HUMAN_CORRECTION_CONFIDENCE = 0.95;

export interface Claim {
  id: string;
  type: ClaimType;
  subject: string;
  content: string;
  scope: Scope;
  project: string | null; // where it is filed; null on a project-scope claim means unsorted
  origin?: string | null; // what the writer said it was working in; never changes
  session?: string | null;
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

// The two-session rule measures recurrence, not independent evidence: one client can open two
// sessions. Requiring the supporting sessions to span `minGapMs` makes "it came up again later" the
// thing being counted, and makes faking it slow.
export function recurs(members: Claim[], k: number, minGapMs: number): boolean {
  if (members.length < k) return false;
  const firstSeen = new Map<string, number>();
  for (const m of members) {
    const t = Date.parse(m.created_at);
    for (const s of m.source_ids) firstSeen.set(s, Math.min(firstSeen.get(s) ?? t, t));
  }
  if (firstSeen.size < k) return false;
  const times = [...firstSeen.values()];
  return Math.max(...times) - Math.min(...times) >= minGapMs;
}

export interface Policy {
  k: number;
  minGapMs: number;
  // Whether recurrence may accept this group's survivor; false holds it as a candidate (used when
  // the agreement check finds the supporting claims conflicted).
  autoAccept: (c: Claim) => boolean;
}
export const DEFAULT_POLICY: Policy = { k: 2, minGapMs: 0, autoAccept: () => true };

export interface GroupKey { subject: string; scope: Scope; project: string | null; user: string | null }
// `version` is the group's live-row count and newest updated_at at read time; apply() refuses a plan
// whose group changed since (a correction or new claim landed), so a stale plan can't undo it.
// `support` is the claims whose recurrence justifies the promotion, for an agreement check.
export interface GroupPlan { key: GroupKey; version: string; supersede: string[]; promote: string[]; support: string[] }

export function groupVersion(members: Claim[]): string {
  return `${members.length}:${members.reduce((m, c) => (c.updated_at > m ? c.updated_at : m), "")}`;
}

export function groupKey(c: Claim): GroupKey {
  return { subject: c.subject, scope: c.scope, project: c.project, user: c.scope === "user" ? c.user : null };
}

// arbiter.consolidate, exact-subject phases only. Per group of live claims on one subject (and owner,
// for the user scope):
// - a candidate survivor is promoted once it recurs across sessions; everything else is superseded;
// - an accepted survivor keeps newer candidates of the same authority alive as pending UPDATES (an
//   older accepted claim must not silently eat its own correction). Once the updates recur, the
//   strongest update is promoted and the old answer superseded.
export function planGroup(members: Claim[], policy: Policy = DEFAULT_POLICY): GroupPlan {
  const best = survivor(members);
  const plan: GroupPlan = { key: groupKey(best), version: groupVersion(members), supersede: [], promote: [], support: [] };
  const updates = best.status === "accepted"
    ? members.filter((m) => m.status === "candidate" && m.authority === best.authority && m.updated_at > best.updated_at)
    : [];
  if (updates.length && recurs(updates, policy.k, policy.minGapMs) && policy.autoAccept(best)) {
    const winner = survivor(updates);
    plan.promote.push(winner.id);
    plan.support = updates.map((u) => u.id);
    plan.supersede.push(...members.filter((m) => m.id !== winner.id).map((m) => m.id));
    return plan;
  }
  const pending = new Set(updates.map((u) => u.id));
  plan.supersede.push(...members.filter((m) => m.id !== best.id && !pending.has(m.id)).map((m) => m.id));
  // Why: accept only when >= K distinct claims from distinct sessions agree; one session's guess must not become the swarm's truth.
  if (best.status === "candidate" && recurs(members, policy.k, policy.minGapMs) && policy.autoAccept(best)) {
    plan.promote.push(best.id);
    plan.support = members.map((m) => m.id);
  }
  return plan;
}

export function groupClaims(live: Claim[]): Claim[][] {
  const groups = new Map<string, Claim[]>();
  for (const c of live) {
    const key = JSON.stringify(groupKey(c));
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }
  return [...groups.values()];
}

export const hasWork = (p: GroupPlan) => p.supersede.length > 0 || p.promote.length > 0;

export function planConsolidation(live: Claim[], policy: Policy = DEFAULT_POLICY): GroupPlan[] {
  return groupClaims(live).map((m) => planGroup(m, policy)).filter(hasWork);
}

// view.compact_line, minus code grounding (the Worker has no checkout to resolve anchors against;
// it names the first anchored path instead).
const CLIP = 240;
export function compactLine(c: Claim): string {
  const mark = c.status === "accepted" ? "✓" : "?";
  const auth = c.authority === "human" ? " 👤" : c.authority === "verified" ? " ☑" : "";
  const body = c.content.split(/\s+/).filter(Boolean).join(" ");
  let line = `${c.id.slice(0, 8)} [${c.type} ${mark}${c.confidence.toFixed(1)}${auth} @${level(c)}] ${c.subject} — ${body.slice(0, CLIP)}`;
  if (body.length > CLIP) line += `… (expand ${c.id.slice(0, 8)} for full)`;
  const path = c.code_refs.find((r) => r && typeof r === "object" && typeof r.path === "string")?.path;
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
