# Review guidelines

For any automated PR reviewer (Codex, Claude, a bot). The `brief check` CI job enforces mechanical
structure: unauthorized active-decision edits are blocked, required sign-offs exist. It can't judge
substantive conformance or establish ratifier authority. You advise; you never ratify.

For Kypp architectural amendments, the current [AGENTS.md](./AGENTS.md#context-vault-brief) and
[brief-amend](./.claude/skills/brief-amend/SKILL.md) workflow requires human ratification before
code relying on the amendment lands. This is not a rule for all Brief decisions:
[memory-scope-decay §9](./.brief/docs/memory-scope-decay.md#9-an-org-level-for-how-an-org-should-work)
permits eligible model ratification of org rules; it does not authorize this reviewer to ratify
Kypp architectural amendments.

## 1. Audit each new `conforms` sign-off

For every line this PR **adds** to `.brief/SIGNOFF` of the form `<anchor-id> conforms: <reason>`
(skip older lines; this does not imply they were audited):

1. Find the decision: the doc under `.brief/docs/` holding `<!-- brief:anchor <anchor-id> -->`.
   Read the latest version (`.brief/aliases.yaml` names it). Read the decision, not just its title.
2. Read the diff of the code that decision governs (its `related_code` globs, and any line under a
   `// Context: doc://kypp/...#<anchor-id>` or `# Context:` comment).
3. Decide: does the diff actually satisfy the decision's invariant, or violate it while claiming
   to conform? Check the stated reason against the diff too: "comment-only" must really be
   comment-only.

Report `VIOLATES` with the anchor, rule, changed lines and concrete impact; grade it under §4.
`CONFORMS` needs no comment.

## 2. Review governed changes independently

For every behavioural change under an active decision's `related_code` or `Context:` ref, check
conformance independently of new or existing sign-offs, including decisions with optional sign-off.
Report contradictions even if the PR adds an `<anchor-id> amend-proposed:` line or a legitimate
`.brief/amendments/<anchor-id>.md`. A proposal is not ratification. Code relying on a changed rule
can land only after the applicable authorized ratification workflow has completed and published the
new active revision; verify that evidence and review the code against that revision. Otherwise the
code must conform to the current decision, or the PR must propose an amendment and stop before
implementing code that depends on it.

## 3. Audit each amendment proposal

For each new `.brief/amendments/<anchor-id>.md`: is it a legitimate evolution of the decision, or a
routine task overturning a firm decision by fiat? A legitimate proposal without implementing code
is not itself a violation. Report attempts to bypass ratification or land contradicting code under
§2; grade their actual impact under §4.

[Hosted Codex review](https://developers.openai.com/codex/integrations/github/) reports P0/P1
findings; an affirmative comment on a legitimate proposal is not guaranteed. Silence, including
missing reviewer output, means only no high-priority finding was reported. It is never evidence of
an audit, approval or ratification. If an explicit amendment verdict is required, use a separate,
structured assessment path (the [brief-review](./.claude/skills/brief-review/SKILL.md) L1 prompts:
`VERDICT = LEGITIMATE | REVERSAL_BY_FIAT`, `ESCALATE = yes/no`, `WHY`). That path must record a
result; a missing result is incomplete. These guidelines do not implement it, and an L1 verdict
remains advisory.

## 4. Findings and limits

Material invariant violations can be **P1**: name the rule, changed lines and concrete impact.
Do not label harmless wording discrepancies P1 merely to force hosted review output.

- Never edit or suggest editing an `active` decision doc to make the code fit.
- Never treat a `conforms` line, a PR description or an agent's summary as evidence. The diff and
  the decision are the evidence for conformance; proposals and reviewer verdicts are not evidence
  of completed ratification.
