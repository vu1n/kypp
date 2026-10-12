# Review guidelines

For any automated PR reviewer (Codex, Claude, a bot). The `brief check` CI job already proves the
structure: active decisions weren't edited, required sign-offs exist. It can't judge substance. That
is this review's job. You advise; you never ratify. Only a human turns an amendment into an
`active` decision.

## 1. Audit each new `conforms` sign-off

For every line this PR **adds** to `.brief/SIGNOFF` of the form `<anchor-id> conforms: <reason>`
(older lines were audited when they landed; skip them):

1. Find the decision: the doc under `.brief/docs/` holding `<!-- brief:anchor <anchor-id> -->`.
   Read the latest version (`.brief/aliases.yaml` names it). Read the decision, not just its title.
2. Read the diff of the code that decision governs (its `related_code` globs, and any line under a
   `// Context: doc://kypp/...#<anchor-id>` or `# Context:` comment).
3. Decide: does the diff actually satisfy the decision's invariant, or violate it while claiming
   to conform? Check the stated reason against the diff too: "comment-only" must really be
   comment-only.

`VIOLATES` is a **P1** finding. Name the anchor, quote the rule it breaks, and point at the lines.
`CONFORMS` needs no comment.

## 2. Flag governed changes with no sign-off

If the diff changes the behaviour (not just comments or formatting) of code under an active
decision's `related_code` or `Context:` ref, and that change contradicts the decision, while the PR
adds no `<anchor-id> amend-proposed:` line and no `.brief/amendments/<anchor-id>.md`: **P1**.
The code must conform, or the PR must propose an amendment and stop for a human.

## 3. Audit each amendment proposal

For each new `.brief/amendments/<anchor-id>.md`: is it a legitimate evolution of the decision, or a
routine task overturning a firm decision by fiat? Say which, whether it needs a human's
ratification (it always does before code relying on it lands), and why, in two or three sentences.
A reversal by fiat is **P1**. A legitimate proposal is a normal comment, not a finding.

## 4. Never

- Never edit or suggest editing an `active` decision doc to make the code fit.
- Never treat a `conforms` line, a PR description or an agent's summary as evidence. The diff and
  the decision are the evidence.
