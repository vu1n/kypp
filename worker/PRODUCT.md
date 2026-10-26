# Product

<!-- impeccable:product-schema 1 -->

Scope: the hosted Worker's own web pages (OAuth consent, pairing approval, paired apps). The rest of
kypp is an MCP server and a CLI with no visual surface. Facts come from README.md, worker/README.md
and the code; lines marked *(inferred)* were not confirmed in an interview.

## Platform

web

## Users

The owner of one kypp install: the person who deployed the Worker (Vu at kypp.vu1n.dev, or anyone
using the Deploy to Cloudflare button) and holds its owner passphrase. Nobody else can complete any
of these pages. The owner arrives mid-task from another app: a coding agent (Claude Code opens the
browser on `/authorize`), a claude.ai connector, or a chat integration such as Huddles that shows a
pairing code and a link. *(inferred)* Pairing links are often opened on a phone from a chat message;
sign-in from a coding agent happens in a desktop browser.

## Product Purpose

kypp is shared memory for coding agents. These pages are its only gates: they let the owner decide,
with the passphrase, which apps may read and write their memory (consent), which integrations may
create private room memory (pairing), and which paired integrations to unlink (paired apps). Success
is a correct decision in a few seconds: allow what the owner just started, refuse anything else.

## Positioning

A single-owner, self-hosted memory server: no accounts, no sign-up, one passphrase. The pages ask
only what the owner can verify (a matching code, where access is going) and say plainly what the
app's name can't prove.

## Operating Context

- Consent: an OAuth authorization request from an MCP client that registered itself. The page shows
  the client's self-asserted name, the redirect host, and a warning when access goes to a loopback
  address. Allow needs the passphrase; Deny does not.
- Pairing: an integration starts a pairing, shows a 9-character code (`XXXX-XXXX`, no vowels, no
  0/O/1/I), and links to `/pair/<code>`. Codes expire in 10 minutes and redeem once. The owner can
  approve, deny, or continue the room memory of an earlier, unlinked pairing.
- Paired apps: `/pairs` lists live paired grants behind the passphrase and unlinks one.
- Passphrase attempts are rate limited (5 a minute per IP).

## Capabilities and Constraints

- Pages are server-rendered strings from a Cloudflare Worker (`src/index.ts`, `src/pairing.ts`):
  no client JavaScript, no build step, no static assets, no external hosts. Forms post back to the
  same URL.
- Text the pages must keep: the
  pairing page says the display name is self-asserted, and continuing an earlier link says it hands
  the app that grant's room memory.
- The passphrase is never echoed back into a page.

## Brand Commitments

The name is lowercase "kypp". No logo, palette or typeface has been chosen. *(inferred)*

## Evidence on Hand

No screenshots, marketing copy or brand assets exist. Demonstration data in reviews is synthetic
(app labels like "Huddles workspace Launch", fake codes and dates).

## Product Principles

- The decision is the page: what is asking, where access goes, what approving allows.
- Say what kypp can't verify, every time it matters.
- Refusing is always one tap and never needs the passphrase.
- No ceremony: the owner should be back in the app they came from within seconds.
