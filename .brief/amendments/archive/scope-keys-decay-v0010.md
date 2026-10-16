# Amendment proposal: scope-keys-decay

**Decision:** doc://kypp/memory-scope-decay@0009#scope-keys-decay
**Proposed by:** Claude, dev project thread "Pin static tokens to projects"   **Date:** 2026-10-16
**Status:** accepted by vu1n merging [PR #41](https://github.com/vu1n/kypp/pull/41)
on 2026-10-16 at 12:20 UTC (commit `18063878c0251a7c76189f2615c4d56d31a8554e`).
This recovery records that merge; it does not introduce a new approval or change the proposal.

## What should change

Add to §8 (placement), after item 1:

> **Pinned tokens.** An operator may pin a `KYPP_API_TOKENS` token to a list of registered projects
> (`name@projA|projB:token`). For such a caller the origin comes only from the pin: the
> `X-Kypp-Project` header or the `project` argument selects among pinned projects (a single pin is
> the default), and a name outside the pin is refused with an error, never remapped and never
> answered with the registered-project list. Its project-scope writes must name a pinned project, so
> they never land unsorted; §8.1's "a write with no origin, or an unknown one, still lands" does not
> apply to it. Defrag's *File* step chooses only among the pin for any claim that token wrote, and a
> pinned issuer (§11) links rooms only inside its pin. Unpinned tokens and OAuth grants keep §8.1 as
> written.

## Why

The model-supplied `project` argument outranks the header, so any static-token caller could read and
claim into every registered project, and an unknown name answered with the full project list. Room
tokens are already pinned by their credential (§11.2); a headless integration's token needs the same
confinement without becoming a room.

## Code that needs it

- `worker/src/tokens.ts`: parses the optional `@proj|proj` pin.
- `worker/src/index.ts`: puts the pin on `Caller.projects`, passes pins to defrag and to `/rooms`.
- `worker/src/mcp.ts` `pinnedTool`: resolves the project only from the pin; refuses outside names and
  unsorted claims; `expand` hides claims outside the pin and its groups.
- `worker/src/defrag.ts`: files a pinned token's claims only inside its pin.
- `worker/src/rooms.ts`: a pinned issuer may link rooms only inside its pin.

## Impact / risk

Tokens without `@` are unchanged, so existing deployments keep today's behaviour. A pinned token's
claim without a project now fails instead of landing unsorted, which is the point. Rooms a pinned
issuer linked before it was pinned keep their links until re-registered.


---
ratified_rev: 0010
ratified_by: vu1n (merge of #41, https://github.com/vu1n/kypp/pull/41; 18063878c0251a7c76189f2615c4d56d31a8554e)
ratified_via: human
