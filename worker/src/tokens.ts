// tokens.ts — parsing KYPP_API_TOKENS, kept apart from index.ts so tests can load it without the OAuth provider.

// Static tokens for clients that can't do the browser flow: "name:token,name:token". A name may
// carry a pin, "name@projA|projB:token", confining that token to those registered projects.
export interface ApiToken { name: string; projects?: string[] }
export function apiTokens(list: string | undefined): Map<string, ApiToken> {
  const m = new Map<string, ApiToken>();
  for (const pair of (list ?? "").split(",")) {
    const i = pair.indexOf(":");
    if (!(i > 0 && pair.length - i > 16)) continue;
    const label = pair.slice(0, i).trim(), at = label.indexOf("@");
    const name = (at < 0 ? label : label.slice(0, at)).trim();
    if (!name) continue;
    // Why: "name@" pins to nothing rather than to everything, so a typo fails closed.
    const projects = at < 0 ? undefined : label.slice(at + 1).split("|").map((p) => p.trim()).filter(Boolean);
    m.set(pair.slice(i + 1).trim(), projects ? { name, projects } : { name });
  }
  return m;
}

// Pinned static-token names and their projects, for defrag: a claim such a token wrote is only ever
// filed inside its pin.
export function tokenPins(list: string | undefined): Map<string, string[]> {
  const pins = new Map<string, string[]>();
  for (const t of apiTokens(list).values()) {
    // Why: two secrets under one name and different pins file only into projects both allow.
    if (t.projects) pins.set(t.name, pins.has(t.name) ? pins.get(t.name)!.filter((p) => t.projects!.includes(p)) : t.projects);
  }
  return pins;
}
