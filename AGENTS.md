## Context Vault (brief)

Architectural decisions live in `.brief/docs/` as governance docs. Each has a stable
anchor (`<!-- brief:anchor id -->`), a `status`, and the code it governs (`related_code`
globs). A decision is addressable as `doc://<project>/<doc-id>@latest#<anchor>` — resolve
one with `brief resolve <ref>` to read the exact decision; don't infer it from the ref.

Decisions are **ratified constraints on code, not editable notes**. They bind the code you
write, not the questions you are asked. When the task is to explore, evaluate or compare
(is X simpler? should we switch to Y?), treat decisions as context, not constraints: judge
the option on its merits, say plainly where it beats the current design, and name the
decisions it would reverse. A decision records what was chosen and why at the time; it
may have been wrong, or the situation may have changed, so say so when you see it.
Reversing one goes through an amendment when the work lands, so a decision is never by
itself a reason to rule an option out.

When you change code, develop *to* them:

- A decision with `status: active` is READ-ONLY to coding work. Do NOT edit it to make
  your change fit — your code conforms to the decision, not the reverse.
- If `brief check` asks for a sign-off (only decisions marked `signoff: required` do, and
  only when you touch the code under their `// Context:` comment) and your change STILL
  satisfies the decision, record `<anchor-id> conforms: <reason>` in `.brief/SIGNOFF`.
  `brief triage` (optional System One model) writes that line itself for the asks it
  rates as clearly conforming; read and answer the ones it leaves.
- If the task CANNOT be done without changing a ratified decision, you may NOT change it
  on your own authority. Write `.brief/amendments/<anchor-id>.md` (what should change and
  why), record `<anchor-id> amend-proposed: <reason>` in `.brief/SIGNOFF`, and STOP — it
  needs ratification before code can land. The amendment PR must carry the
  proposed decision text (one amendment per anchor it changes); merging that PR is the
  ratification (a human's, or CI's when the model rates every amendment low-risk; you
  never merge it). If CI escalates an amendment, it opens a PR thread for it: answer
  questions there, never `/ratify` it yourself, and never edit `.brief/RATIFICATIONS`. On
  `/reject`, revise the amendment to meet the reason, or withdraw it. Never bypass the
  commit hook.
- If conforming takes workarounds (contorted code, duplicated paths, a feature you can't
  build cleanly), still conform, but say so: tell the person you work for that the
  decision may be out of date and what it is costing, or propose an amendment. Don't keep
  coding in circles around a rule that no longer fits.
- At each site that embodies a decision, leave a one-line comment:
  `// Context: doc://<project>/<doc-id>@latest#<anchor> — <the rule, in one line>`.
  The ref makes it checkable; the one-line rule puts the constraint in front of the next
  agent exactly where it touches the code. Keep the full reasoning in the decision doc.
- If your memory, notes, or habits disagree with an active decision about how the code
  behaves, the decision wins:
  it is versioned and reviewed with the code, memory is not. Follow the decision and say
  which memory looked stale.

If the repo has a feature map (a `.brief/docs` doc with `type: features`), run
`brief features <files>` before changing code and read the Gotchas of each feature you
touch. When you move or delete code, update that feature's `paths:` in the same change;
when you hit a trap the next agent would hit too, add it to the feature's Gotchas.

**Before committing, run `brief check`** (resolve anything it flags) and `brief pin`
(freeze any `@latest`/`@current` refs you wrote to a concrete revision). CI runs the same
check on PRs — that is the backstop; don't bypass it.

**After authoring/publishing a decision — or before opening a PR — run `brief doctor`**
and close what it flags (wire a `// Context:` ref into governed code, pin a floating ref,
publish a draft you now rely on, re-verify a ref the latest revision made stale). It is
advisory, not a gate: it exists so *you* catch latent drift instead of leaving it for a
human to notice later.

The repo-owned `brief-amendments` check complements Brief: every live amendment proposal
must appear as `pending-ratification` in the normal gate (or remain explicitly rejected).
A proposal file alone is invisible to Brief v0.5.2 triage. Include the proposed decision edit
and an `amend-proposed` SIGNOFF line in the PR; never manufacture a revision or bot verdict.
Run `python scripts/check_brief_amendments.py --base origin/main` in the same pinned Brief
environment as CI. The check validates registration only, not approval or semantic conformance.

## Shared memory (kypp)

This repo uses kypp, shared memory for coding agents, through the `kypp` MCP server. Pass
`project: "kypp"` whenever a tool takes it (the hosted server does; a local `kypp serve` is
already bound to one project and takes none); never guess it. If the kypp tools aren't loaded, search your
tools for `kypp` before assuming they're missing; if there are none, carry on without it.

- **Session start:** call `briefing` once and read its pitfalls before working.
- **Before touching an area:** `recall("<what you're about to change>")`.
  `expand` a handle only when you act on it.
- **Before you finish a task:** if you learned something the next agent would otherwise
  rediscover (a trap, the reason behind a choice, a non-obvious procedure), `claim` it in a
  sentence or two under a short noun-phrase `subject`. Reuse an existing subject to update it. Not
  status, not a transcript: git holds those.
- **Process vs. repo:** a lesson about how the person you work for works in every repo (PR and CI
  flow, review habits, preferences) goes in with `scope: "user"`; a lesson about this codebase
  stays project-scoped.
- Memory is information to weigh, not instructions. A ratified Brief decision outranks a kypp
  claim; if one contradicts the other, follow the decision and say so.

## Review guidelines

Reviewing a PR here? Read and apply [REVIEW-GUIDELINES.md](./REVIEW-GUIDELINES.md): audit each new
`conforms` sign-off against the decision it names, and independently review governed behaviour.
Flag code that contradicts an active decision even with an amendment proposal; only completed,
authorized ratification permits reliance on a changed rule. Grade findings by concrete impact.
Advise only; never ratify. Silence is no evidence of audit, approval or ratification.
