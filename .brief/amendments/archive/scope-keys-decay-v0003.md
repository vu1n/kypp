# Amend `scope-keys-decay`: an org level for how an org should work

Proposed 2026-10-11, from Vu in the CI-minutes thread: "how a user works within an org and how an org
_should_ work — maybe if multiple users are doing the same thing it can be ratified, or an org wide
decision is made and gets pushed to kypp to propagate."

## Why change

§8 gives memory two axes: *where* a lesson was learned (project, widened to a group by recurrence) and
*who* it is about (`user`, chosen at write time, read in every repo). Neither one holds a practice an
org expects of everyone who works in its repos. Example: "open PRs as drafts and run checks in the
session first, because private CI minutes are capped."

- A `user` claim reaches only that person's sessions. A collaborator's agent in the same repos never
  sees it.
- A project claim can widen to its group only when the same lesson recurs in two or more repos, and
  §8.4 forbids widening across a person or org boundary. A practice that one person writes down once
  can never reach the org.

## What changes

1. **An org is a group at the top of the roll-up.** It is a row in `project_parents` named for the
   GitHub owner (for example `vu1n`), with every repo under that owner as a child. No new scope value:
   an org claim is `scope='group'` with the org's name in `project`, so cascading reads already return
   it to every session whose project rolls up to the org, whoever the user is.
2. **Agents still never write to the org.** The `claim` tool keeps `project` and `user` only. An org
   claim gets there by one of two routes, and both need a person.
   - **Ratified recurrence.** When `user`-scope claims on the same lesson come from two or more
     distinct users working in repos under one org, defrag writes an org-level *proposal*: a
     candidate at the org that cites the user claims it came from. The pass uses the same S1
     "same lesson?" scorer as dedupe, and the model can hold a proposal back but never cause one.
     A proposal is not accepted on recurrence, because it crosses a person boundary. An org owner
     ratifies it from the operator CLI (`kypp ratify <handle>`), which accepts it at `human`
     authority. Until then it shows only in recall, labelled "proposed for the org".
   - **Pushed decisions.** A decision the org ratified in Brief (a `.brief/docs` decision whose
     frontmatter marks it org-wide) is synced to kypp as an accepted org claim. The claim holds the
     `doc://` ref and the one-line rule, never the decision body: Brief stays the source of truth, and
     a new revision of the doc re-syncs the claim. Sync runs from the operator CLI or the org's CI
     with an operator token (`kypp sync-decisions <repo>`), never over MCP, so `authority-order`
     holds: no MCP path writes `human` authority.
3. **One-person orgs** (a personal account, where there is no second user to recur) get an operator
   shortcut: `kypp promote <handle> --to <org>` copies a `user` claim to the org at `human` authority
   and cites the original. Same CLI-only rule.
4. **Reads.** The briefing lists accepted org claims under their own heading ("how this org works"),
   ahead of the user's own process, because an org rule applies to every contributor. Recall labels
   org lines `@org:<name>`. If an org claim and a user claim conflict on a subject, `authority-order`
   decides as it does today. A ratified or synced org claim is `human` authority and wins over agent
   claims.
5. **Decay.** Org claims run the `sessions` clock from §4 like other process claims. A synced decision
   claim is superseded when its doc is retired or superseded, never by the clock alone.

Unchanged: agents pick only project (implicitly) or `user`. Widening between levels stays one step at
a time and never reaches global automatically. The S1 model never accepts or widens anything. History
stays append-only.

## Open

- **Org membership.** Which users belong to an org: inferred from the repos their sessions write in
  (an origin under the owner), or from the GitHub org's members via the OAuth identity. The first
  needs no new scopes; the second is exact for real orgs but not for personal accounts.
- **Proposal threshold.** Two distinct users is the starting value; measure on real claims.
- **Where org decisions live.** Each repo's `.brief/docs` with an `org: true` flag, or one org repo
  (such as `vu1n/dev`) that holds org-wide decisions only.


---
ratified_rev: 0003
ratified_by: Vu
