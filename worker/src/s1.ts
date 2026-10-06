// s1.ts — the optional System One write gate (Jev, Clef) over the TypeSafe API, the Worker's twin of
// kypp/s1.py. A System One model returns calibrated probabilities, never text. It filters and labels
// what enters memory and can hold back a promotion it finds conflicted; it never accepts a claim or
// changes its scope. No key → no gate.
import { TypeSafeClient } from "@typesafe-ai/sdk";
import { TYPES, type ClaimType } from "./memory.ts";

export interface S1Env {
  TYPESAFE_API_KEY?: string;
  TYPESAFE_BASE_URL?: string;
  TYPESAFE_DEFAULT_MODEL?: string;
  KYPP_S1?: string;
}

// The slice of TypeSafeClient the gate uses, so tests can pass a fake.
export interface S1Client {
  systemOne(req: { state: unknown; questions: Record<string, unknown> }): PromiseLike<{ answers: Record<string, any> }>;
}

export function s1Client(env: S1Env): S1Client | null {
  if (!env.TYPESAFE_API_KEY || ["0", "off", "false", "no"].includes((env.KYPP_S1 ?? "").toLowerCase())) return null;
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

const prob = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1 ? v : null);

// Fail open: any error is "no opinion", never a drop.
export async function judge(client: S1Client | null, draft: { subject: string; content: string; project: string | null },
  deadlineMs = DEADLINE_MS): Promise<Verdict> {
  const none: Verdict = { keep: null, type: null, general: null };
  if (!client) return none;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("s1 deadline")), deadlineMs); });
    const { answers } = await Promise.race([client.systemOne({ state: draft, questions: QUESTIONS }), late]);
    const t = answers.type;
    const type = t?.type === "choice" && TYPES.includes(t.choice) && (prob(t.confidence) ?? 0) >= RETYPE_ABOVE ? (t.choice as ClaimType) : null;
    return {
      keep: answers.keep?.type === "noul" ? prob(answers.keep.noul) : null,
      type,
      general: answers.general?.type === "noul" ? prob(answers.general.noul) : null,
    };
  } catch {
    return none;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
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
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const late = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("s1 deadline")), deadlineMs); });
    const { answers } = await Promise.race([client.systemOne({ state: { claims }, questions: AGREE }), late]);
    return answers.agree?.type === "noul" ? prob(answers.agree.noul) : null;
  } catch {
    return null;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}
