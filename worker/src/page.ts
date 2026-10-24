// page.ts — the one shell every kypp web page renders in (consent, pairing, paired apps): the stylesheet
// (worker/DESIGN.md), the header naming this install, and the response headers. No script, no
// external host: each page is one HTML string.

export const escape = (v: string) => v.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);

const MONO = `ui-monospace,"SF Mono",Menlo,Consolas,monospace`;
const STYLE = `:root{color-scheme:light dark;--ground:light-dark(#f5f7f6,#0f1412);--raised:light-dark(#fff,#161d1a);
--ink:light-dark(#16201b,#e3eae6);--muted:light-dark(#4b5952,#a1afa8);--line:light-dark(#cfd8d3,#2c3833);--field:light-dark(#7a8781,#66756d);
--accent:light-dark(#0f6a50,#5ec8a4);--on-accent:light-dark(#fff,#03201a);--danger:light-dark(#b1261d,#ff9187);
--note:light-dark(#fff4d6,#2a2210);--note-line:light-dark(#d7a33a,#806423);--note-ink:light-dark(#4a3300,#f2d894)}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--ground);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;caret-color:var(--accent)}
::selection{background:color-mix(in srgb,var(--accent) 30%,transparent)}
.wrap{max-width:34rem;margin:0 auto;padding:1rem 1rem 3rem}
header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:.25rem 1rem;padding:.25rem 0 .75rem;border-bottom:1px solid var(--line);margin-bottom:2.5rem}
.brand{font-weight:750;font-size:1.0625rem;letter-spacing:-.01em}
.host{font:.875rem/1.4 ${MONO};color:var(--muted);overflow-wrap:anywhere}
h1{font-size:1.375rem;line-height:1.3;font-weight:650;letter-spacing:-.015em;margin:0 0 .75rem;text-wrap:balance}
h1 svg{vertical-align:-.15em;margin-right:.4rem}
p{margin:0 0 1rem}
p,dd,li{text-wrap:pretty}
.lede{color:var(--muted);margin-bottom:1.5rem}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:.2em}
a:hover{text-decoration-thickness:2px}
.mono{font-family:${MONO}}
.code{display:block;margin:0 0 .5rem;padding:.875rem 1rem;background:var(--raised);border:1px solid var(--line);border-radius:10px;
font:600 2rem/1.2 ${MONO};letter-spacing:.14em;text-align:center;font-variant-numeric:tabular-nums}
.grant{margin:0 0 1.25rem;padding:.875rem 1rem;background:var(--raised);border:1px solid var(--line);border-radius:10px}
.grant dt{color:var(--muted);font-size:.875rem}
.grant dd{margin:0;font-weight:600;overflow-wrap:anywhere}
.grant dd+dt{margin-top:.75rem;padding-top:.75rem;border-top:1px solid var(--line)}
.grant .target{font:600 1.5rem/1.25 ${MONO};letter-spacing:.01em}
.note{display:flex;gap:.625rem;align-items:flex-start;margin:0 0 1.25rem;padding:.75rem .875rem;background:var(--note);border:1px solid var(--note-line);border-radius:10px;color:var(--note-ink)}
.note.ok{background:color-mix(in srgb,var(--accent) 9%,var(--raised));border-color:color-mix(in srgb,var(--accent) 45%,var(--line));color:var(--ink)}
.note.info{background:var(--raised);border-color:var(--line);color:var(--ink)}
.i-ok{color:var(--accent)}
.i-idle{color:var(--muted)}
.note svg,.err svg{flex:none;margin-top:.2em}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.err{display:flex;gap:.5rem;align-items:flex-start;margin:.375rem 0 0;color:var(--danger);font-weight:600}
.field{display:block;font-weight:600;margin:1.75rem 0 0}
.field input{display:block;margin-top:.375rem}
.hint{color:var(--muted);font-size:.875rem;margin:.375rem 0 0}
input[type=password]{width:100%;min-height:44px;padding:.5rem .75rem;font:inherit;color:inherit;background:var(--raised);border:1px solid var(--field);border-radius:8px}
input[aria-invalid=true]{border-color:var(--danger)}
input[aria-invalid=true]:focus-visible{outline-color:var(--danger)}
input[type=radio]{accent-color:var(--accent);width:1.125rem;height:1.125rem;margin:.15em 0 0;flex:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.actions{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem;margin:1.25rem 0 0}
button{font:inherit;font-weight:600;min-height:44px;padding:.5rem 1.25rem;border:1px solid var(--line);border-radius:8px;background:var(--raised);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--muted)}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
button.primary:hover{background:color-mix(in srgb,var(--accent) 85%,var(--ink))}
button.danger{color:var(--danger)}
button.quiet{min-height:44px;padding:.5rem 0;border:0;background:none;color:var(--accent);font-weight:500;text-decoration:underline;text-underline-offset:.2em}
fieldset{margin:1.75rem 0 0;padding:0;border:0;min-width:0}
legend{font-weight:600;padding:0;margin-bottom:.5rem}
.choices{list-style:none;margin:0;padding:0;background:var(--raised);border:1px solid var(--line);border-radius:10px}
.choices li+li{border-top:1px solid var(--line)}
.choices label{display:flex;gap:.75rem;align-items:flex-start;padding:.75rem 1rem;cursor:pointer}
.choices small{display:block;color:var(--muted);font-size:.875rem}`;

// Drawn, not glyphs: one 20px, 2px-stroke family.
const svg = (paths: string, tone = "") => `<svg${tone ? ` class="${tone}"` : ""} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
export const icon = {
  warning: svg(`<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>`),
  alert: svg(`<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>`),
  check: svg(`<circle cx="12" cy="12" r="10"/><path d="m8 12.5 2.5 2.5L16 9.5"/>`, "i-ok"),
  x: svg(`<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>`, "i-idle"),
  info: svg(`<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>`, "i-idle"),
};

/**
 * The owner passphrase field. A form's error is shown right under it (every refusal is about the
 * passphrase or what it unlocks); the field is then marked invalid, points at it, and takes focus.
 */
export const passphrase = (o: { error?: string; focus?: boolean; hint?: string } = {}) =>
  `<label class="field">Owner passphrase<input type="password" name="secret" autocomplete="current-password" required${
    o.error ? ` aria-invalid="true" aria-describedby="err"` : ""}${o.error || o.focus ? " autofocus" : ""}></label>${
    o.error ? `<p class="err" id="err" role="alert">${icon.alert}<span>${escape(o.error)}</span></p>` : ""}${
    o.hint ? `<p class="hint">${o.hint}</p>` : ""}`;

/** A whole page: `title` is the tab's name, `body` the main column's HTML (already escaped). */
export function render(base: string, title: string, body: string): string {
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>${escape(title)} · kypp</title><style>${STYLE}</style>
<div class="wrap"><header><span class="brand">kypp</span><span class="host">${escape(new URL(base).host)}</span></header>
<main>${body}</main></div>`;
}

/** An HTML response that is never cached or framed. `headers` (e.g. a consent cookie) are kept. */
export function htmlResponse(markup: string, status = 200, headers = new Headers()): Response {
  headers.set("Content-Type", "text/html; charset=utf-8");
  headers.set("Cache-Control", "no-store");
  headers.set("Content-Security-Policy", "frame-ancestors 'none'");
  headers.set("X-Frame-Options", "DENY");
  return new Response(markup, { status, headers });
}
