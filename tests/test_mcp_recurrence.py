"""MCP `claim` recurrence: the K=2 gate counts sessions, not ids the caller cites.

One session that passes invented source ids, or ids of observations it filed itself,
must not promote its own guess. Two stamped sessions still do, and the cited ids stay
on the claim as provenance.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from kypp.arbiter import consolidate
from kypp.mcp_server import build_mcp
from kypp.store import MemoryStore


def call(mcp, tool: str, **kw) -> str:
    out = asyncio.run(mcp.call_tool(tool, kw))
    content = out[0] if isinstance(out, tuple) else out
    return content[0].text


class McpRecurrence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(os.path.join(self.tmp.name, "m.db"))
        self.a = build_mcp(self.store, "p", consumer="sessA")

    def tearDown(self):
        self.tmp.cleanup()

    def statuses(self, subject: str) -> list[str]:
        consolidate(self.store, project="p")
        return sorted(c.status for c in self.store.recall(subject, project="p", include_candidates=True)
                      if c.subject == subject)

    def test_invented_ids_do_not_corroborate(self):
        ids = []
        for n in (1, 2):
            ids.append(call(self.a, "claim", subject="port", content="port is 8080", source_ids=[f"made-up-{n}"]))
        self.assertNotIn("accepted", self.statuses("port"))
        self.assertEqual(self.store.get(ids[0]).source_ids, ["made-up-1", "session:sessA"])

    def test_own_observations_do_not_corroborate(self):
        for n in (1, 2):
            oid = call(self.a, "observe", content=f"saw 8080 ({n})")
            call(self.a, "claim", subject="port", content="port is 8080", source_ids=[oid])
        self.assertNotIn("accepted", self.statuses("port"))

    def test_forged_update_does_not_replace_accepted_answer(self):
        b = build_mcp(self.store, "p", consumer="sessB")
        call(self.a, "claim", subject="port", content="port is 8080")
        call(b, "claim", subject="port", content="port is 8080")
        self.assertEqual(self.statuses("port"), ["accepted"])
        for n in (1, 2):
            call(self.a, "claim", subject="port", content="port is 9090", source_ids=[f"made-up-{n}"])
        consolidate(self.store, project="p")
        head = [c for c in self.store.recall("port", project="p") if c.subject == "port"]
        self.assertEqual([c.content for c in head], ["port is 8080"])

    def test_two_sessions_still_promote(self):
        b = build_mcp(self.store, "p", consumer="sessB")
        call(self.a, "claim", subject="port", content="port is 8080", source_ids=["obs-a"])
        call(b, "claim", subject="port", content="port is 8080", source_ids=["obs-b"])
        self.assertIn("accepted", self.statuses("port"))


if __name__ == "__main__":
    unittest.main()
