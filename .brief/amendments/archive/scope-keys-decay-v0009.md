# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0008#scope-keys-decay
**Proposed by:** Claude, Huddles project thread "Kypp memory for huddle bots"
**Date:** 2026-10-15
**Status:** Vu chose this on the thread's decision card ("Re-seat on continue").

## What should change

1. **§11.1: a seat may expire.** An issuer may give a seat an expiry; its token stops working after it.
   Huddles sets it to the bot's room-access expiry, so a bot whose access ends loses the room's
   memory without any further call.
2. **§11.7: continuing a grant brings its rooms back, not its seats.** Continuing a revoked paired grant
   restores its rooms and their memory but closes every seat it had. The app seats its current bots
   again. A bot removed while the app was unlinked couldn't be unseated then, so it stays out.

## Why

Review of @0008: a bot removed while unlinked kept its seat, and continuing revived it; an expired bot
kept a working seat in a lane other bots now write to.


---
ratified_rev: 0009
ratified_by: Vu
