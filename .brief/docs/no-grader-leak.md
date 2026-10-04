---
id: no-grader-leak
project: kypp
type: decision
status: active
title: "Never put grader internals into a claim"
related_code:
  - "kypp/distill.py"
  - "kypp/wire.py"
---

<!-- brief:anchor signal-only-rubric -->
## Record failed-criteria counts, never grader text

When a run's rubric fails, the distiller records a signal-only pitfall: the count of failed criteria and the grader scheme. Verbatim criterion names and grader feedback stay in the observation layer.

**Why.** Grader internals (hidden test names, tmp paths) in a shared, prompt-injectable claim leak the benchmark and invite Goodhart. This happened once and was fixed.

### Invariant
- A distilled claim never contains a grader's verbatim criterion names or feedback.
