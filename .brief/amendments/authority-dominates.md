# Require sign-off on `authority-order`

Change: add `signoff: required` to the frontmatter. No change to the rule.

Why: Brief v0.3.0 made sign-off opt-in, reserved for decisions where silent drift is dangerous. This one is a trust boundary. If an MCP path could write `authority=human` or `verified`, one confused or prompt-injected agent could override verified facts across the whole swarm. A change under its `// Context:` comments should get a deliberate `conforms` line.
