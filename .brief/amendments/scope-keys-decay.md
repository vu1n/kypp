# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0006#scope-keys-decay
**Proposed by:** Claude, Huddles project thread "Kypp memory for huddle bots"
**Date:** 2026-10-14
**Status:** Proposal only; human ratification required. This file grants no authority.

## What should change

Replace §11.7 (**Issuing and revoking**) so an integration can be granted the right to issue rooms
through a pairing code that the kypp owner approves, alongside operator configuration:

7. **Issuing and revoking.** An integration may register rooms only with an *issuer grant*. A grant
   comes from one of two places:
   - **Operator configuration**, as today: a `KYPP_API_TOKENS` name listed in `KYPP_ROOM_ISSUERS`.
   - **Pairing.** The integration asks kypp to pair (`POST /pair` with a display name). kypp answers
     with a secret pairing handle that the integration keeps, a short user code, and a URL showing
     that code. A person opens the URL, checks the code matches what the integration shows, and the
     kypp owner approves with the owner passphrase. The integration then redeems its handle once
     for an issuer token. Codes expire after 10 minutes and redeem only once. The approval page
     says the display name is self-asserted, as the OAuth consent page does.

   An issuer token's only power is `POST /rooms` and `DELETE /rooms/<id>` for its own rooms. It
   never reads or writes memory. Each grant is its own issuer namespace (`room:<issuer>:<id>`), so
   two paired workspaces can't collide or revoke each other's rooms. Links (point 3) are limited to
   registered projects, as today.

   A registered room returns a per-room bearer token once, and kypp keeps only its digest. Revoking
   a room token makes that lane unreachable. Revoking an issuer grant revokes every room it issued
   and stops new ones. The integration can revoke its own grant (unlink), the owner can revoke any
   paired grant, and the operator can remove a configured name. Claims stay as history
   (append-only), and an operator can still read them.

Points 1–6 and 8 are unchanged.

## Why

Today, turning on room memory takes three manual steps by two people: a kypp operator adds a token
to `KYPP_API_TOKENS` and its name to `KYPP_ROOM_ISSUERS`, and a Huddles operator pastes that token
into a `KYPP_MEMORY` secret. Vu asked for a link through a URL with a one-time code, verified on
both ends, and picked a pairing code over OAuth because it works with any client. OAuth can come
later.

The flow follows the OAuth device-authorization pattern (RFC 8628), without needing an OAuth client:

- **kypp's end:** the owner sees the requester's name and the code, and approves with the owner
  passphrase.
- **The integration's end:** only the holder of the secret pairing handle can redeem the approval.

The resulting token is narrower than today's operator token, which is a full API token that also
has the issuer right.

## What stays safe

- A room's audience doesn't change: room tokens remain lane-pinned (points 1–2).
- An issuer grant can't mint authority (point 8), read memory, or reach another issuer's rooms.
- A guessed or leaked user code is useless without the owner passphrase, and a leaked approval page
  is useless without the integration's handle.
- `POST /pair` is unauthenticated, so it is rate limited, and pending pairings expire.
- Revoking a grant ends every room it created.
