# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0007#scope-keys-decay
**Proposed by:** Claude, Huddles project thread "Kypp memory for huddle bots"
**Date:** 2026-10-14
**Status:** Proposal; Vu asked for it in the thread ("ratify the change and do both").

## What should change

1. **§11.1: a lane belongs to the room, not to one bot.** A room is the integration's chat room. Each bot the
   integration seats there gets its own *member* token for the same lane (`POST /rooms {id, member}`),
   and removing a bot revokes only its member token (`DELETE /rooms/<id>/members/<member>`). Bots
   come and go, and the room keeps its memory. Every member reads and writes the one lane
   `room:<issuer>:<id>`; members are not separate principals.
2. **§11.7: revoking a grant pauses it, and a new pairing may resume it.** Revoking an issuer grant
   (unlink, or the owner on `/pairs`) makes every token under it stop working. Rooms, members and
   claims are kept. When approving a new pairing, the owner may choose to *continue* an earlier
   revoked paired grant instead of starting a new one. The new token then takes over that grant's
   namespace, and its rooms and member tokens work again. The page says that continuing gives the
   app that grant's room memory, and only revoked grants can be continued (a live grant can't be
   taken over). Paired grants still can't link rooms to repos.

## Why

Vu: unlinking shouldn't lose the memory; it should stay server side. Today the lessons are kept,
but a re-link pairs as a fresh random issuer, so every lane key changes and the old lanes can't be
reached again. Today a lane also belongs to one bot participation, so removing a bot or adding a
second Claude bot to the room starts from empty. Vu: "we should form some sort of unique identifier
for huddle+room".

## Risk

An owner tricked into continuing an earlier grant for a phishing app would hand that app the
grant's room lanes (not repos, not user or global memory). This is the same trust as approving any
pairing, with a stronger warning on the page.


---
ratified_rev: 0008
ratified_by: Vu


---
ratified_rev: 0008
ratified_by: Vu
