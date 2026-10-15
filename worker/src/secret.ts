// secret.ts — constant-time comparison for the Worker's shared secrets (passphrase, API and operator tokens).
const enc = new TextEncoder();

export async function sameSecret(a: string, b: string): Promise<boolean> {
  // Hash both sides first so lengths match for the constant-time compare.
  const [ha, hb] = await Promise.all([a, b].map((v) => crypto.subtle.digest("SHA-256", enc.encode(v))));
  return crypto.subtle.timingSafeEqual(ha, hb);
}
