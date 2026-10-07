// consolidate.ts — the cleanup pass: plan each subject group, let the S1 model hold back a promotion
// whose supporting claims conflict, then apply with the stale-read guard.
import { type Claim, type GroupPlan, type Policy, groupClaims, hasWork, planGroup } from "./memory.ts";
import { HOLD_BELOW, type S1Client, agrees } from "./s1.ts";
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

export async function consolidate(store: D1Store, policy: Policy, s1: S1Client | null,
  filter?: Parameters<D1Store["liveClaims"]>[0]) {
  return store.apply(await planWithJudge(await store.liveClaims({ ...filter, filed: true }), policy, s1));
}
