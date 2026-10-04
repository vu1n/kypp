# Require sign-off on `verify-operator-only`

Change: add `signoff: required` to the frontmatter. No change to the rule.

Why: Brief v0.3.0 made sign-off opt-in, reserved for decisions where silent drift is dangerous. This one is a security boundary. A verify command is arbitrary shell that `kypp verify` runs, so an MCP path that accepts one would be remote code execution for whoever runs the next sweep. A change under its `// Context:` comment should get a deliberate `conforms` line.


---
ratified_rev: 0002
ratified_by: Vu
