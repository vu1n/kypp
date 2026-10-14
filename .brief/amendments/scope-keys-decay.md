# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0005#scope-keys-decay
**Proposed by:** Claude, Huddles project thread "Kypp memory for huddle bots"
**Date:** 2026-10-14
**Status:** Proposal only; human ratification required. This file grants no authority.

## What should change

Add §11, **room lanes**: private memory for a chat room's bot. A room is an audience (who may see a
lesson), not a level on the origin ladder (where a lesson applies), so it sits beside §8 and
never inside its roll-up.

1. **A room lane is a principal.** Each room bot gets its own kypp identity, `room:<id>`, issued
   to that one bot participation. Its claims are stored with `scope='room'`, keyed by that
   identity, the same way `user` claims are keyed by the person. The lane's audience is the
   room's bot and nobody else; another room, a coding agent or the operator's `owner` session
   never sees it in `recall` or `briefing`.
2. **The credential pins everything.** For a room identity the server sets origin and lane from
   the credential. The `project` argument, the `X-Kypp-Project` header and the `scope` argument
   are ignored. Reads and writes never mention or list registered projects (the "Known
   projects" note is omitted).
3. **What a room bot reads:** its own lane, plus the origins its room is *linked* to, which cascade
   as §8.2 describes. Links are set by whoever manages the room, outside the model. No link means
   the lane only. A room bot reads no `user` scope and no `global`.
4. **Inside the lane, the usual lifecycle.** The write gate (§2 and §8.2's injection guard)
   applies. Candidates are accepted when they recur in two sessions, so two wakes count. Event
   clocks (§4) decay them.
5. **Nothing leaves a lane automatically.** Defrag never files, widens or promotes a room claim,
   and recurrence across rooms is not a reason to widen: rooms with different people saying
   similar things have not agreed to share.
6. **Leaving the lane is an explicit share.** A room identity can write a `project` claim only at
   one of its room's linked origins, and only through an explicit share (a `share_to` argument
   naming the linked origin). The claim records `shared_from: room:<id>` and then follows §8 like
   any origin claim. The bot is told to share only when someone in the room asked for it. The
   server cannot check that, so the link is the hard boundary: the manager opts a room into each
   repo it may write to. This is §10.7's rule applied to claims, where the person disclosing needs
   permission for the whole audience of the destination.
7. **Issuing and revoking.** An operator-configured integration (a `KYPP_API_TOKENS` name, for
   example `huddles`) registers a room with its links and receives a per-room bearer token once.
   Kypp keeps only the token's digest. Revoking the token makes the lane unreachable. Its claims
   stay as history (append-only), and an operator can still read them.
8. **No authority is minted.** Room claims and shared claims are agent authority; §6 and
   `authority-order` are unchanged.

Extend `related_code` to cover the room registration route and its migration when they are
built. Every other section is unchanged.

## Why

Huddles bots (one bot per room) want memory that carries across wakes. Today a headless token
can't be confined. The model-supplied `project` argument beats the header, so a bot can read or
claim into any registered repo. An unknown room name lands unsorted and is then filed into a repo
by defrag. Every unknown-origin read also lists the registered projects. Treating rooms as
origins on the §8 ladder would make cross-room recurrence widen private talk. A principal lane
reuses what `user` scope already does (private to one identity, read from wherever it's
attached) for a group audience, and keeps §8's automatic arranging for the repo axis where it
belongs.

## Open

- Forgetting on request (a person asks the bot to forget something they said) collides with
  append-only history. This amendment doesn't solve it. Until a decision does, revoking the room
  is the only removal.
