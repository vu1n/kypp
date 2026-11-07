// memory.ts — the governance rules, ported from kypp/arbiter.py and kypp/view.py. Pure functions
// over Claim rows (no D1), so the node tests exercise exactly what the Worker runs. Keep these in
// step with the Python originals: local and cloud stores must rank and promote the same way.

export const TYPES = ["fact", "preference", "decision", "procedure", "artifact", "hypothesis", "pitfall"] as const;
export const AUTHORITIES = ["agent", "verified", "human"] as const;
export type ClaimType = (typeof TYPES)[number];
export type Authority = (typeof AUTHORITIES)[number];
// Scopes: this repo, the signed-in user across repos, everyone. The agent is a label, not a scope.
// `group` is a level in the roll-up table (a group or org several projects share); its claims carry the
// group's name in `project`. Only defrag widens a claim into one; agents never write it.
// `room` is a chat room's private lane, keyed by the room's identity in `user`
// like a person's scope; it is an audience, not a level, so nothing widens out of it.
export const SCOPES = ["project", "group", "user", "global", "room"] as const;
export type Scope = (typeof SCOPES)[number];

// An origin only files into a project the operator registered; an unknown name never creates one.
// An exact name wins; then a registered path the origin ends with ("https://github.com/vu1n/kypp.git"
// → "vu1n/kypp"); then the last path segment, so "vu1n/Kypp" resolves to "kypp". Each fallback
// counts only when it names exactly one project: two registered "foo"s leave the claim unsorted.
const GIT_SUFFIX = /\.git$/i;
const path = (s: string) => s.trim().replace(GIT_SUFFIX, "").toLowerCase().split("/").filter(Boolean);
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
  if (c.scope === "project") return c.project ?? "unsorted";
  return c.scope === "group" ? `group:${c.project}` : c.scope;
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
  verify?: string | null; // the operator's freshness command; set only through POST /v1/operator
  evidence?: string | null; // how the writer knew it: what they ran or read (metadata.evidence)
  pointer?: boolean;      // set by recall when a path, anchor or doc:// ref in the query matched the claim, not its words
  confirmations?: Confirmation[]; // set by consolidate: other sessions whose newest feedback on this claim is `used`
  disputed?: boolean;     // set by reads: a session rated it `wrong` and no later `used` cleared it
}

// A session that was shown the claim, acted on it, and rated it `used` with a note on what it checked.
export interface Confirmation { session: string; at: string }

// Rank survivors by authority first; agent < verified < human.
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

// Distinct sessions/observations backing a subject (arbiter._corroboration). MCP claims here store only
// the server's session stamp, never caller ids, so every entry counts.
export function corroboration(members: Claim[]): number {
  return new Set(members.flatMap((m) => m.source_ids)).size;
}

// The two-session rule measures recurrence, not independent evidence: one client can open two
// sessions. Requiring the supporting sessions to span `minGapMs` makes "it came up again later" the
// thing being counted, and makes faking it slow.
// A `used` rating counts as its session's support at the rating's time, because the rater checked the
// claim against its own work; re-claiming what it was shown (an echo) still counts for nothing.
export function recurs(members: Claim[], k: number, minGapMs: number): boolean {
  const firstSeen = new Map<string, number>();
  const seen = (s: string, t: number) => firstSeen.set(s, Math.min(firstSeen.get(s) ?? t, t));
  for (const m of members) {
    const t = Date.parse(m.created_at);
    for (const s of m.source_ids) seen(s, t);
    // A session never confirms its own claim.
    for (const c of m.confirmations ?? []) if (c.session !== m.session) seen(`session:${c.session}`, Date.parse(c.at));
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
// `confirmed` is the (claim, session) `used` ratings the promotion counted; apply() refuses the plan if one was withdrawn since.
export interface GroupPlan { key: GroupKey; version: string; supersede: string[]; promote: string[]; support: string[]; confirmed?: Rated[] }
export interface Rated { claim: string; session: string; at: string }

const countedConfirmations = (members: Claim[]): Rated[] =>
  members.flatMap((m) => (m.confirmations ?? []).filter((c) => c.session !== m.session).map((c) => ({ claim: m.id, session: c.session, at: c.at })));

export function groupVersion(members: Claim[]): string {
  return `${members.length}:${members.reduce((m, c) => (c.updated_at > m ? c.updated_at : m), "")}`;
}

// Scopes keyed by a principal (a person, a room) group per owner, so two rooms never consolidate together.
export const ownedScope = (scope: Scope) => scope === "user" || scope === "room";

export function groupKey(c: Claim): GroupKey {
  return { subject: c.subject, scope: c.scope, project: c.project, user: ownedScope(c.scope) ? c.user : null };
}

// arbiter.consolidate, exact-subject phases only. Per group of live claims on one subject (and owner,
// for the user scope):
// - a candidate survivor is promoted once it recurs across sessions; everything else is superseded;
// - an accepted survivor keeps newer candidates of the same authority alive as pending UPDATES (an
//   older accepted claim must not silently eat its own correction). Once the updates recur, the
//   strongest update is promoted and the old answer superseded.
// An echo that changes what it echoes is a pending update to the survivor: it has no recurrence credit, but its
// change is kept, not superseded, until updates recur or a later pass retires it.
const sameText = (a: string, b: string) => a.split(WHITESPACE).filter(Boolean).join(" ") === b.split(WHITESPACE).filter(Boolean).join(" ");
const echoUpdate = (m: Claim, best: Claim) => m.status === "candidate" && !m.source_ids.length && m.authority === best.authority
  && m.created_at > best.created_at && !sameText(m.content, best.content);

export function planGroup(members: Claim[], policy: Policy = DEFAULT_POLICY): GroupPlan {
  const best = survivor(members);
  const plan: GroupPlan = { key: groupKey(best), version: groupVersion(members), supersede: [], promote: [], support: [] };
  // Promotion bumps the survivor's updated_at, so an echo update kept through it is matched by when it was written.
  const updates = best.status === "accepted"
    ? members.filter((m) => m.status === "candidate" && m.authority === best.authority && (m.updated_at > best.updated_at || echoUpdate(m, best)))
    : [];
  if (updates.length && recurs(updates, policy.k, policy.minGapMs) && policy.autoAccept(best)) {
    const winner = survivor(updates);
    plan.promote.push(winner.id);
    plan.support = updates.map((u) => u.id);
    plan.confirmed = countedConfirmations(updates);
    plan.supersede.push(...members.filter((m) => m.id !== winner.id).map((m) => m.id));
    return plan;
  }
  // Why: accept only when >= K distinct claims from distinct sessions agree; one session's guess must not become the swarm's truth.
  const promoting = best.status === "candidate" && recurs(members, policy.k, policy.minGapMs) && policy.autoAccept(best);
  // An unstamped survivor (an echo) never retires a stamped candidate: that claim is the subject's evidence, and the
  // one a later `used` rating names.
  const keep = new Set([...updates, ...members.filter((m) => echoUpdate(m, best))].map((u) => u.id));
  if (!promoting && best.status === "candidate" && !best.source_ids.length) {
    for (const m of members) if (m.status === "candidate" && m.source_ids.length) keep.add(m.id);
  }
  plan.supersede.push(...members.filter((m) => m.id !== best.id && !keep.has(m.id)).map((m) => m.id));
  if (promoting) {
    plan.promote.push(best.id);
    plan.support = members.map((m) => m.id);
    plan.confirmed = countedConfirmations(members);
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

// What the write gate reads: the lesson and, when given, how it is known, since agents read both.
export const gatedText = (content: string, evidence?: string | null) => (evidence ? `${content}\n\nHow it is known: ${evidence}` : content);

// view.compact_line, minus code grounding (the Worker has no checkout to resolve anchors against;
// it names the first anchored path instead).
const CLIP = 240;
const EVIDENCE_CLIP = 100;
const WHITESPACE = /\s+/;
export function compactLine(c: Claim): string {
  const mark = c.status === "accepted" ? "✓" : "?";
  const auth = c.authority === "human" ? " 👤" : c.authority === "verified" ? " ☑" : "";
  const body = c.content.split(WHITESPACE).filter(Boolean).join(" ");
  let line = `${c.id.slice(0, 8)} [${c.type} ${mark}${c.confidence.toFixed(1)}${auth} @${level(c)}] ${c.subject} — ${body.slice(0, CLIP)}`;
  if (body.length > CLIP) line += `… (expand ${c.id.slice(0, 8)} for full)`;
  const path = c.code_refs.find((r) => r && typeof r === "object" && typeof r.path === "string")?.path;
  if (path) line += ` → ${path}`;
  const how = (c.evidence ?? "").split(WHITESPACE).filter(Boolean).join(" ");
  // A claim that says how it is known is marked apart from a bare assertion; expand gives the whole of it.
  line += how ? ` [checked: ${how.slice(0, EVIDENCE_CLIP)}${how.length > EVIDENCE_CLIP ? "…" : ""}]` : "";
  if (c.disputed) line += " ⚠ a session rated this wrong; check it before relying on it";
  return line;
}

export function renderClaims(claims: Claim[], empty = "(no matching memory)", line: (c: Claim) => string = compactLine): string {
  return claims.map(line).join("\n") || empty;
}

// The briefing, one heading per type in briefingOrder's order.
export function renderBriefing(claims: Claim[], line: (c: Claim) => string = compactLine): string {
  if (!claims.length) return "(no accepted memory yet)";
  const out: string[] = [];
  for (const c of claims) {
    const head = `# ${c.type}`;
    if (!out.includes(head)) out.push(head);
    out.push(line(c));
  }
  return out.join("\n");
}

// The briefing loads into every session, so its claim lines share one budget: a longer briefing is
// followed less, and a wrong line in it misleads every session.
export const BRIEFING_CHARS = 4000;
export const shortLine = (c: Claim) =>
  `${c.id.slice(0, 8)} [${c.type} ${c.status === "accepted" ? "✓" : "?"}${c.confidence.toFixed(1)} @${level(c)}] ${c.subject} (expand for the lesson)`;

// Lines in render order: full while the budget lasts, then subject-only, then left out. Returns how each
// kept claim renders; a claim not in the map was left out.
export function fitBriefing(claims: Claim[], budget = BRIEFING_CHARS): Map<string, "full" | "short"> {
  const fit = new Map<string, "full" | "short">();
  let left = budget;
  for (const c of claims) {
    const full = compactLine(c).length + 1, short = shortLine(c).length + 1;
    if (full <= left) { fit.set(c.id, "full"); left -= full; }
    else if (short <= left) { fit.set(c.id, "short"); left -= short; }
  }
  return fit;
}

// view.briefing_claims ordering: traps first, then choices made, then how-tos. Stable sort keeps
// the store's strength order within a type.
const BRIEFING_PRIORITY: Record<string, number> = { pitfall: 0, decision: 1, procedure: 2 };
export function briefingOrder(claims: Claim[], limit: number): Claim[] {
  return [...claims].sort((a, b) => (BRIEFING_PRIORITY[a.type] ?? 3) - (BRIEFING_PRIORITY[b.type] ?? 3)).slice(0, limit);
}

// An FTS5 MATCH expression from free text: each word quoted (so FTS syntax in the query can't
// break the parse), OR-joined like the local keyword recall. Empty when the query has no words.
// Why: words are Unicode letters, marks, digits and _, so `café` or `日本語` reach MATCH whole and
// unicode61 folds them the way it folded the stored text. unicode61 doesn't segment CJK, so `日本語`
// matches only a stored run that ends at a space or punctuation.
const WORDS = /[\p{L}\p{M}\p{N}_]+/gu;
export function ftsQuery(text: string): string {
  return (text.match(WORDS) ?? []).map((t) => `"${t}"`).join(" OR ");
}
