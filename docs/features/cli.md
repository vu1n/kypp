---
id: cli
paths:
  - "kypp/cli.py"
  - "kypp/__init__.py"
  - "pyproject.toml"
  - "uv.lock"
entry:
  - "kypp/cli.py"
tests:
  - "tests/test_project_cli.py"
---
# CLI entrypoint

`kypp <command>` dispatches to each module's `main()`. Imports are lazy, so `serve` never
loads the capture stack.
