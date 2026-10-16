"""Exercise the real pinned Brief gate, including the proposal-only PR41 gap."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from brief.gate import check
from brief.versions import publish
from scripts.check_brief_amendments import orphaned_proposals


DOC = """---
id: placement
project: fixture
type: decision
status: active
related_code:
  - worker.py
---
<!-- brief:anchor placement -->
## Placement
Unknown-origin writes always land unsorted.
"""


class AmendmentRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.brief = self.repo / ".brief"
        self.write(".brief/docs/placement.md", DOC)
        self.write("worker.py", "accept_unknown = True\n")
        publish(self.brief, "placement")
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, capture_output=True, text=True).stdout

    def write(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def proposal(self, anchor="placement"):
        self.write(f".brief/amendments/{anchor}.md", "# Proposal\nRefuse unknown origins for pinned tokens.\n")

    def test_pr41_shape_fails_even_when_brief_gate_is_green(self):
        self.proposal()
        self.write("worker.py", "accept_unknown = False\n")
        self.commit()
        self.assertEqual(check(self.repo, self.brief, self.base), [])
        self.assertEqual(orphaned_proposals(self.repo, self.base),
                         [".brief/amendments/placement.md"])

    def test_proposed_decision_and_signoff_enter_triage(self):
        self.proposal()
        self.write(".brief/docs/placement.md", DOC.replace("always land unsorted", "are refused for pinned tokens"))
        self.write(".brief/SIGNOFF", "placement amend-proposed: pinned exception\n")
        self.commit()
        self.assertTrue(any(v.kind == "pending-ratification"
                            for v in check(self.repo, self.brief, self.base)))
        self.assertEqual(orphaned_proposals(self.repo, self.base), [])

    def test_signoff_without_decision_edit_is_still_not_triage(self):
        self.proposal()
        self.write(".brief/SIGNOFF", "placement amend-proposed: pinned exception\n")
        self.commit()
        self.assertEqual(orphaned_proposals(self.repo, self.base),
                         [".brief/amendments/placement.md"])

    def test_preexisting_orphan_is_not_hidden_by_an_unrelated_pr(self):
        self.proposal()
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()
        self.write("README.md", "Unrelated edit\n")
        self.commit()
        self.assertEqual(orphaned_proposals(self.repo, self.base),
                         [".brief/amendments/placement.md"])

    def test_unknown_anchor_is_rejected(self):
        self.proposal("unknown")
        self.commit()
        self.assertEqual(orphaned_proposals(self.repo, self.base),
                         [".brief/amendments/unknown.md"])

    def test_archived_proposals_are_not_pending(self):
        self.write(".brief/amendments/archive/placement-v0001.md", "Historical proposal\n")
        self.commit()
        self.assertEqual(orphaned_proposals(self.repo, self.base), [])

    def test_staged_proposal_is_checked_before_commit(self):
        self.proposal()
        self.git("add", ".")
        self.assertEqual(orphaned_proposals(self.repo),
                         [".brief/amendments/placement.md"])

    def test_unstaged_deletion_cannot_hide_staged_proposal(self):
        self.proposal()
        self.git("add", ".")
        (self.brief / "amendments/placement.md").unlink()
        self.assertEqual(orphaned_proposals(self.repo),
                         [".brief/amendments/placement.md"])

    def test_untracked_proposal_is_not_part_of_staged_change(self):
        self.proposal()
        self.assertEqual(orphaned_proposals(self.repo), [])
