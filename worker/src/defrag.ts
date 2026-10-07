// defrag.ts — the hourly pass: judge claims the write gate missed, file unsorted claims into a known
// project, then consolidate (supersede duplicates, promote subjects that recur).
import { consolidate } from "./consolidate.ts";
import { type Claim, type Policy, resolveOrigin } from "./memory.ts";
import { CONTROL_ABOVE, type ProjectInfo, type S1Client, fileUnder, judge } from "./s1.ts";
import type { D1Store } from "./store.ts";

// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — filing puts an unsorted claim into a known project; origin is kept, the move is logged, and an unsure claim stays unsorted.
export async function fileClaim(store: D1Store, s1: S1Client | null, c: Pick<Claim, "id" | "subject" | "content" | "origin">,
  projects: ProjectInfo[]): Promise<string | null> {
  // A project registered after the claim was written files it by name, with no model.
  const named = resolveOrigin(c.origin, projects.map((p) => p.name));
  if (named) return (await store.file(c.id, named, null, "origin matches a registered project")) ? named : null;
  const pick = await fileUnder(s1, { subject: c.subject, content: c.content, origin: c.origin ?? null }, projects);
  if (!pick) return null;
  return (await store.file(c.id, pick.project, pick.confidence, "model choice")) ? pick.project : null;
}

// Each unfiled claim can cost one model call, so a pass takes a bounded batch and rotates through the rest.
const BATCH = 25;

// Context: doc://kypp/memory-scope-decay@0002#scope-keys-decay — reads are a channel between agents, so control text the gate missed at write time is held back late.
// The gate fails open, so a claim written while S1 was down got no verdict. Judge it now; control text
// aimed at agents is rejected, anything else just keeps its verdict.
export async function judgeLate(store: D1Store, s1: S1Client | null): Promise<number> {
  if (!s1) return 0;
  let rejected = 0;
  for (const c of await store.unjudged(BATCH)) {
    const v = await judge(s1, { subject: c.subject, content: c.content, project: c.project });
    if (v.control !== null && v.control > CONTROL_ABOVE) {
      if (await store.reject(c.id, `control text (p_control=${v.control.toFixed(2)})`)) rejected++;
    } else {
      await store.judged(c.id, v.keep === null && v.control === null ? null : { keep: v.keep, general: v.general, type: v.type, control: v.control, late: true });
    }
  }
  return rejected;
}

export async function defrag(store: D1Store, policy: Policy, s1: S1Client | null) {
  const held = await judgeLate(store, s1);
  const projects = await store.projects();
  let filed = 0;
  for (const c of await store.unsorted(BATCH)) {
    if (await fileClaim(store, s1, c, projects)) filed++;
    else await store.fileTried(c.id);
  }
  return { held, filed, ...(await consolidate(store, policy, s1)) };
}
