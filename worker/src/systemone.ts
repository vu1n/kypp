// systemone.ts — POST /v1/systemone, the TypeSafe API's System One route answered by Clef on the
// Worker's AI binding, so any TypeSafe client (first: `brief triage` in CI through typesafe-sdk) can
// set TYPESAFE_BASE_URL to this Worker. A pure proxy: it never reads or writes the claims store.
export const SYSTEM_ONE_PATH = "/v1/systemone";
const MODELS = new Set(["clef", "clef-flash"]);

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const fail = (status: number, error: string) => json({ error }, status);
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

// `tokenName` maps a bearer to its KYPP_API_TOKENS name, or null; OAuth tokens are not accepted here.
export async function handleSystemOne(request: Request, ai: Ai | undefined,
  tokenName: (token: string) => Promise<string | null>): Promise<Response> {
  const bearer = /^Bearer\s+(\S+)\s*$/i.exec(request.headers.get("Authorization") ?? "")?.[1];
  if (!bearer || !(await tokenName(bearer))) return fail(401, "a kypp API token is required as the bearer");
  if (request.method !== "POST") return fail(405, "use POST");
  if (!ai) return fail(503, "this Worker has no AI binding");
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return fail(400, "the body must be JSON");
  }
  if (!isObject(body) || !("state" in body) || !isObject(body.questions) || !Object.keys(body.questions).length) {
    return fail(400, "the body needs state and at least one question");
  }
  // typesafe-sdk always sends a model (its own default is jev-latest), so a missing one is clef.
  const model = (body.model ?? "") === "" ? "clef" : body.model;
  if (typeof model !== "string" || !MODELS.has(model)) return fail(400, `unknown model; use one of: ${[...MODELS].join(", ")}`);
  let out: unknown;
  try {
    out = await ai.run(`@cf/cloudflare/${model}` as any, { model, state: body.state, questions: body.questions } as any);
  } catch {
    return fail(502, "the model call failed");
  }
  if (!isObject(out) || !isObject(out.answers)) return fail(502, "the model returned no answers");
  // The SDK requires `model` and `usage`; Workers AI may leave either out.
  return json({ model: typeof out.model === "string" && out.model ? out.model : model, usage: isObject(out.usage) ? out.usage : {}, answers: out.answers });
}
