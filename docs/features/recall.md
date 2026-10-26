---
id: recall
paths:
  - "kypp/view.py"
  - "kypp/shell.py"
  - "kypp/remote.py"
entry:
  - "kypp/view.py"
  - "kypp/shell.py"
tests:
  - "tests/test_remote.py"
  - "tests/test_rank_oracle.py"
---
# Recall, briefing and handles

`kypp recall`, `briefing`, `show`, `remember`, `correct`, `reject` and `usage` print one compact
handle line per claim, and `show` expands a handle to the full claim. With `KYPP_URL` set, all but
`usage` go to the hosted Worker (`kypp/remote.py`) and never open the local store.

## Gotchas
- The briefing is the same top-N accepted claims for every task. It is not task-conditioned
  (that's the unbuilt `compose`).
- `kypp usage --record` writes usage rows, but nothing in kypp ever fills `claim_usages.score`.
