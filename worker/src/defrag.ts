// defrag.ts — the hourly pass that arranges memory: file unsorted claims into a known project, then
// plan each subject group, let the S1 model hold back a promotion whose supporting claims conflict,
// and apply with the stale-read guard.
import { type Claim, type GroupPlan, type Policy, groupClaims, hasWork, planGroup } from "./memory.ts";
import { HOLD_BELOW, type S1Client, agrees, fileTo } from "./s1.ts";
import type { D1Store } from "./store.ts";

// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — the model can only hold a promotion back; recurrence is what accepts.
export async function planWithJudge(live: Claim[], policy: Policy, s1: S1Client | null): Promise<GroupPlan[]> {
  const plans: GroupPlan[] = [];
  for (const members of groupClaims(live)) {
    let plan = planGroup(members, policy);
    if (plan.promote.length && s1) {
      const byId = new Map(members.map((m) => [m.id, m]));
      const p = await agrees(s1, plan.support.map((id) => byId.get(id)!).map(({ subject, content }) => ({ subject, content })));
      if (p !== null && p < HOLD_BELOW) plan = planGroup(members, { ...policy, autoAccept: () => false });
    }
    if (hasWork(plan)) plans.push(plan);
  }
  return plans;
}

// Promote and supersede within subject groups (all of them, or the ones `filter` names).
export async function settle(store: D1Store, policy: Policy, s1: S1Client | null,
  filter?: Parameters<D1Store["liveClaims"]>[0]) {
  return store.apply(await planWithJudge(await store.liveClaims(filter), policy, s1));
}

// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — unsorted claims are filed into one known project; an origin that is now known files without the model.
// `projects` is the server's KYPP_PROJECTS list, or null to file into projects that already hold claims.
export async function fileUnsorted(store: D1Store, s1: S1Client | null, projects: string[] | null): Promise<number> {
  const pending = await store.unsorted();
  if (!pending.length) return 0;
  const known = projects ?? await store.filedProjects();
  let filed = 0;
  for (const c of pending) {
    if (c.origin && known.includes(c.origin)) {
      if (await store.file(c.id, c.origin, { by: "origin" })) filed++;
      continue;
    }
    const f = await fileTo(s1, { subject: c.subject, content: c.content, project: c.origin ?? null }, known);
    if (f && await store.file(c.id, f.project, { by: "s1", p: f.p })) filed++;
  }
  return filed;
}

export async function defrag(store: D1Store, policy: Policy, s1: S1Client | null, projects: string[] | null = null) {
  const filed = await fileUnsorted(store, s1, projects);
  return { filed, ...(await settle(store, policy, s1)) };
}
