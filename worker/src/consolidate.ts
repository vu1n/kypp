// consolidate.ts — the cleanup pass: plan each subject group, let the S1 model hold back a promotion
// whose supporting claims conflict, then apply with the stale-read guard.
import { type Claim, type GroupPlan, type Policy, groupClaims, hasWork, planGroup } from "./memory.ts";
import { HOLD_BELOW, type S1Client, agrees } from "./s1.ts";
import type { D1Store } from "./store.ts";

// The model can only hold a promotion back; recurrence is what accepts.
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

type Filter = Parameters<D1Store["liveClaims"]>[0];

// Live filed claims, each candidate carrying the sessions whose current verdict on it is `used`.
export async function plan(store: D1Store, policy: Policy, s1: S1Client | null, filter?: Filter): Promise<GroupPlan[]> {
  const live = await store.liveClaims({ ...filter, filed: true });
  const confirmed = await store.confirmations(live.filter((c) => c.status === "candidate").map((c) => c.id));
  return planWithJudge(live.map((c) => confirmed.has(c.id) ? { ...c, confirmations: confirmed.get(c.id) } : c), policy, s1);
}

export async function consolidate(store: D1Store, policy: Policy, s1: S1Client | null, filter?: Filter) {
  return store.apply(await plan(store, policy, s1, filter));
}
