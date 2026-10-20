# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0012#scope-keys-decay
**Proposed by:** Claude, at Vu's request. **Date:** 2026-10-20.

## What should change

Replace §11.5 ("Nothing leaves a lane automatically") with a narrow promotion path, and reword §11.6
so the explicit share is the immediate path beside it. Defrag may promote a room claim to one of its
room's linked origins only when the claim is accepted in its lane, the S1 write gate rates it a
general lesson about that repo (never about the people in the room, their preferences or the room's
conduct), and S1 is sure which linked origin it is about. It lands there as a candidate marked
`shared_from: room:<id>` and `promoted_by: defrag`, hidden from other sessions at the origin until a
session there (or another room linked to it) claims the same subject on its own, and then follows §8.
A room never promotes past its links, nor to a group, org or global; from the origin, §8.4 and §9 are
unchanged. The exact text is in the accompanying decision diff.

## Why

Vu, 2026-10-20: bots in Huddles rooms should write memories that roll up to the room, then the
project, then the org; "rooms are just an additional roll up alongside project." Today a room lesson
reaches a repo only when a bot shares it because someone asked, so a lesson a room keeps relearning
about a repo it works on never reaches that repo's coding agents unless someone thinks to ask. The
room stays an audience beside §8's ladder; its links, which its manager already opts into per repo,
become a promotion path for lessons about the linked repo, the way recurrence already widens origin
claims one level.

## Code that needs it

`worker/src/defrag.ts` (a promotion step over changed accepted room claims, reusing filing's S1
calls), `worker/src/store.ts` (a promoted candidate hidden from origin reads until accepted), and
tests for each guard. Huddles needs nothing: huddles-up already registers each room with its linked
repos.

## Impact / risk

- Reverses §11.5's "Defrag never files, widens or promotes a room claim."
- **Consent.** People in a room didn't each ask for a lesson to move. Guards: the manager linked the
  room to that repo, only lessons about the repo qualify, and a lesson must recur in the room and
  again at the repo before anyone there relies on it.
- **Injection.** Anyone who can talk in a linked room can try to steer coding agents' memory. Guards:
  acceptance in the lane first, the write gate's control check, and hiding the promoted candidate
  until an independent session at the repo agrees.
- **Cost.** One more defrag step per changed accepted room claim, with the S1 calls filing already
  makes. Every promotion is logged and the lane claim stays, so a promotion can be undone.


---
ratified_rev: 0013
ratified_by: vu1n via /ratify (https://github.com/vu1n/kypp/pull/49#discussion_r4325003309); vu1n (merge of #49)
ratified_via: human
