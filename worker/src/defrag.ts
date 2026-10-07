// defrag.ts — the hourly pass: file unsorted claims into a known project, then consolidate (supersede
// duplicates, promote subjects that recur).
import { consolidate } from "./consolidate.ts";
import { type Claim, type Policy, resolveOrigin } from "./memory.ts";
import { type ProjectInfo, type S1Client, fileUnder } from "./s1.ts";
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

export async function defrag(store: D1Store, policy: Policy, s1: S1Client | null) {
  const projects = await store.projects();
  let filed = 0;
  for (const c of await store.unsorted()) if (await fileClaim(store, s1, c, projects)) filed++;
  return { filed, ...(await consolidate(store, policy, s1)) };
}
