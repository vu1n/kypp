// s1.ts — the optional System One write gate (Jev, Clef) over the TypeSafe API or Workers AI, the
// Worker's twin of kypp/s1.py. A System One model returns calibrated probabilities, never text. It filters and labels
// what enters memory, files an unsorted claim into a known project, and can hold back a promotion it
// finds conflicted; it never accepts or widens a claim. No key and no AI binding → no gate.
import { TypeSafeClient } from "@typesafe-ai/sdk";
import { TYPES, type ClaimType } from "./memory.ts";

export interface S1Env {
  TYPESAFE_API_KEY?: string;
  TYPESAFE_BASE_URL?: string;
  TYPESAFE_DEFAULT_MODEL?: string;
  KYPP_S1?: string;
  KYPP_S1_MODEL?: string; // Workers AI decision model: "clef" (default) or "clef-flash"
  AI?: Ai;
}

// The slice of TypeSafeClient the gate uses, so tests can pass a fake.
export interface S1Client {
  systemOne(req: { state: unknown; questions: Record<string, unknown> }): PromiseLike<{ answers: Record<string, any> }>;
}

// A TypeSafe key wins; otherwise Clef on the Worker's own AI binding (same System One request and
// answer shape, billed to the Cloudflare account, no key to manage).
export function s1Client(env: S1Env): S1Client | null {
  if (["0", "off", "false", "no"].includes((env.KYPP_S1 ?? "").toLowerCase())) return null;
  if (!env.TYPESAFE_API_KEY) {
    if (!env.AI) return null;
    const ai = env.AI, model = env.KYPP_S1_MODEL || "clef";
    return { systemOne: ({ state, questions }) => ai.run(`@cf/cloudflare/${model}` as any, { model, state, questions } as any) as any };
  }
  // A slow gate stalls the agent's claim call, so cap it; a timeout fails open like any error.
  return new TypeSafeClient({ apiKey: env.TYPESAFE_API_KEY, baseURL: env.TYPESAFE_BASE_URL, defaultModel: env.TYPESAFE_DEFAULT_MODEL,
    timeout: DEADLINE_MS, retry: { maxRetries: 0 } });
}

export const DEADLINE_MS = 5000; // end to end, enforced in judge() too, not just per attempt
export const DROP_BELOW = 0.1;   // same bar as distill.GatedDistiller
const RETYPE_ABOVE = 0.6;        // only relabel a defaulted type when the model is fairly sure

const QUESTIONS = {
  keep: {
    type: "noul",
    instructions: "The state holds a lesson a coding agent wants to save to shared memory. Would a later agent need it, and be unable to get it from the current code, docs or git history?",
    criteria: {
      true: "A durable preference, process, pitfall or decision that holds beyond this session and that the code does not show.",
      false: "Project status (shipped, merged, in progress, PR numbers), a play-by-play step, a fact the code already states, or something only true for this run.",
    },
  },
  type: {
    type: "choice",
    instructions: "What kind of lesson is this?",
    criteria: {
      pitfall: "A trap: something that fails or misleads, and how to avoid it.",
      decision: "A choice that was made, and why.",
      procedure: "How to do something, step by step.",
      preference: "How a person wants work done or communicated.",
      fact: "A durable fact about the system that the code does not make obvious.",
      artifact: "Where something lives: a dashboard, doc, bucket or service.",
      hypothesis: "A suspicion not yet confirmed.",
    },
  },
  general: {
    type: "noul",
    instructions: "Is this lesson true outside the one repository it was learned in?",
    criteria: {
      true: "It is about the person, their tools or general practice, and would hold in any of their repositories.",
      false: "It depends on this repository's code, layout, services or history.",
    },
  },
} as const;

export interface Verdict {
  keep: number | null;          // P(worth remembering); null = no opinion
  type: ClaimType | null;       // a confident type label, or null
  general: number | null;       // P(holds outside this repo)
}

// One System One call under the end-to-end deadline; rejects on error or timeout so callers fail open.
async function ask(client: S1Client, state: unknown, questions: Record<string, unknown>, deadlineMs: number): Promise<Record<string, any>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("s1 deadline")), deadlineMs); });
    return (await Promise.race([client.systemOne({ state, questions }), late])).answers;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

const prob = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1 ? v : null);

// Fail open: any error is "no opinion", never a drop.
export async function judge(client: S1Client | null, draft: { subject: string; content: string; project: string | null },
  deadlineMs = DEADLINE_MS): Promise<Verdict> {
  const none: Verdict = { keep: null, type: null, general: null };
  if (!client) return none;
  try {
    const answers = await ask(client, draft, QUESTIONS, deadlineMs);
    const t = answers.type;
    const type = t?.type === "choice" && TYPES.includes(t.choice) && (prob(t.confidence) ?? 0) >= RETYPE_ABOVE ? (t.choice as ClaimType) : null;
    return {
      keep: answers.keep?.type === "noul" ? prob(answers.keep.noul) : null,
      type,
      general: answers.general?.type === "noul" ? prob(answers.general.noul) : null,
    };
  } catch {
    return none;
  }
}

const AGREE = {
  agree: {
    type: "noul",
    instructions: "The state holds claims about one subject, written to shared memory by coding agents in separate sessions. Do they say the same thing, so that one of them can stand as the agreed answer?",
    criteria: {
      true: "They make the same point, possibly in different words or detail.",
      false: "They contradict each other, or make different points that can't both be the answer.",
    },
  },
} as const;

export const HOLD_BELOW = 0.5; // conflicted: the subject waits as candidates instead of promoting

// P(the supporting claims agree), or null for no opinion. Fails open like judge().
export async function agrees(client: S1Client | null, claims: { subject: string; content: string }[],
  deadlineMs = DEADLINE_MS): Promise<number | null> {
  if (!client || claims.length < 2) return null;
  try {
    const answers = await ask(client, { claims }, AGREE, deadlineMs);
    return answers.agree?.type === "noul" ? prob(answers.agree.noul) : null;
  } catch {
    return null;
  }
}

export const FILE_ABOVE = 0.7; // an unsorted claim is filed only on a confident choice
const MAX_PROJECTS = 60;       // one choice question; past this, filing waits for a narrower list

export interface ProjectInfo { name: string; description: string }

// Which known project an unsorted claim belongs to, or null: no model, no projects, an error, a
// timeout, "none", a name that isn't registered, or a choice under FILE_ABOVE. `origin` is the
// unrecognised name the writer gave, if any; it is a hint, not an answer.
export async function fileUnder(client: S1Client | null, claim: { subject: string; content: string; origin: string | null },
  projects: ProjectInfo[], deadlineMs = DEADLINE_MS): Promise<{ project: string; confidence: number } | null> {
  if (!client || !projects.length || projects.length > MAX_PROJECTS) return null;
  const criteria: Record<string, string> = {};
  for (const p of projects) criteria[p.name] = p.description || `The repository named ${p.name}.`;
  criteria.none = "It is not clearly about any one of these, or it is about the person rather than a repository.";
  const file = { type: "choice", instructions: "The state holds a lesson a coding agent saved without saying which repository it is about. Which one is it about?", criteria };
  try {
    const a = (await ask(client, claim, { file }, deadlineMs)).file, confidence = prob(a?.confidence);
    if (a?.type !== "choice" || a.choice === "none" || confidence === null || confidence < FILE_ABOVE) return null;
    return projects.some((p) => p.name === a.choice) ? { project: a.choice, confidence } : null;
  } catch {
    return null;
  }
}
