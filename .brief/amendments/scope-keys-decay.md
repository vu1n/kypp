# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0006#scope-keys-decay
**Proposed by:** Claude, Huddles project thread "Kypp memory for huddle bots"
**Date:** 2026-10-14
**Status:** Proposal only; human ratification required. This file grants no authority.

## What should change

Replace §11.7 (**Issuing and revoking**) so a kypp owner can grant an integration the right to
issue rooms by consent, not only an operator through configuration:

7. **Issuing and revoking.** An integration may register rooms only with an *issuer grant*. A grant
   comes from one of two places:
   - **Operator configuration**, as today: a `KYPP_API_TOKENS` name listed in `KYPP_ROOM_ISSUERS`.
   - **Owner consent through OAuth**: the integration is an OAuth client of kypp. It sends the owner
     to `/authorize` asking for the `rooms` scope. The consent screen names the client and the
     integration-side account it links (such as "Huddles workspace *Launch*"). The owner approves,
     and the integration exchanges the code (PKCE plus state) for a token whose only power is
     `POST /rooms` and `DELETE /rooms/<id>` for rooms it issued. A `rooms` token never reads or
     writes memory, and an `mcp` token never issues rooms.

   Each grant is its own issuer namespace: rooms are keyed `room:<issuer>:<id>`, so two linked
   workspaces can't collide or revoke each other's rooms. A registered room returns a per-room
   bearer token once, and kypp keeps only its digest. Revoking a room token makes that lane
   unreachable. Revoking an issuer grant (the owner unlinks, or the operator removes the name)
   stops new rooms from being created and revokes every room that grant issued. Claims stay as
   history (append-only), and an operator can still read them.

Points 1–6 and 8 are unchanged. In particular, links (point 3) are still limited to registered
projects, and a consented issuer can link a room only to projects its granting owner may read.

## Why

Today, turning on room memory takes three manual steps by two people: a kypp operator adds a token
to `KYPP_API_TOKENS` and its name to `KYPP_ROOM_ISSUERS`, and a Huddles operator pastes that token
into a `KYPP_MEMORY` secret. Vu asked for a link-by-URL flow that both ends verify. kypp already runs
an OAuth provider with dynamic client registration for its MCP clients, so a consent link gives
exactly that, with no secret anyone copies:

- kypp verifies the integration's identity and the owner's intent on its consent screen.
- The integration verifies the code came from the request it started (PKCE plus state).

The grant is narrower than today's operator token, which is a full API token that also has the
issuer right.

## What stays safe

- A room's audience doesn't change: room tokens remain lane-pinned (points 1–2).
- A consented grant can't mint authority (point 8), read memory, or reach another issuer's rooms.
- Unlinking revokes the whole grant: one action ends every room it created.
