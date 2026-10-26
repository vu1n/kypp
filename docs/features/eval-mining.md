---
id: eval-mining
paths:
  - "kypp/evaltasks.py"
entry:
  - "kypp/evaltasks.py"
---
# Eval-task mining

`kypp mine-tasks <repo>` mines eval-task candidates from transcripts. A session whose tests
pass becomes an auto-gradeable candidate; one without a grader is flagged TODO.
