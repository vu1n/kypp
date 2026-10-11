# Amend `scope-keys-decay` §9: org rules are ratified in Brief, and kypp only syncs them

Proposed 2026-10-11. The owner decided that merging an amendment PR is the ratification and that a
System One model ratifies by default, escalating only reversals and high-impact changes, because every
decision a person must make invites rubber-stamping. §9 as ratified at @0003 still routes org rules
through kypp operator commands (ratify a proposal, promote a `user` claim, sync), each a separate
human act inside kypp.

## What should change in §9

1. **Brief is the only door to the org.** An org rule is a Brief decision in the org's decisions repo
   (one repo per org; this also answers §9's open question of where org-wide decisions live). It is
   ratified the Brief way: a merged PR, auto-ratified by the model unless it escalates.
2. **Cross-user recurrence opens a PR, not a kypp proposal.** When defrag finds the same `user`
   lesson from two or more distinct users under one org, it opens a draft decision PR in the org's
   decisions repo citing the claims, instead of writing an org candidate in kypp. The S1 "same
   lesson?" scorer still only holds a proposal back, never causes one.
3. **One-person orgs** get the same path: promoting a `user` claim is opening that PR (an agent may
   draft it when the owner asks), so the operator `promote` shortcut is dropped.
4. **Sync is the only kypp write.** After `brief ratify` publishes, the org's CI calls the Worker's
   sync route (see the `authority-dominates` amendment). A person-ratified revision lands at `human`,
   a model-ratified one at `verified`. A new revision re-syncs; a retired or superseded doc
   supersedes its claim.
5. **Drop** the operator `ratify` and `promote` commands from the order of work. The order becomes:
   (0) the "how this org works" briefing section, done; (1) decision sync; (2) defrag opening
   cross-user PRs.

Reads and decay (§9.4, §9.5) are unchanged.

## Impact

Agents still never write to the org: an agent can draft a decision PR, but only a merge (and, for
reversals or high-impact rules, a person) ratifies it. The model's power grows from "can hold a
proposal back" to "can ratify a low-impact rule", which is the owner's decision; its rules land at
`verified`, below anything a person ratified.


---
ratified_rev: 0004
ratified_by: Vu
