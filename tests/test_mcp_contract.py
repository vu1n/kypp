"""MCP boundary: declared schemas and effective writes, through the real server.

A skipped import is a failure. Positive calls must succeed. A store that kept a
verifier fails the same assertion the live calls use.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kypp.mcp_server import build_mcp
from kypp.store import MemoryStore


ROOT = Path(__file__).resolve().parents[1]
TOOLS = (
    "observe",
    "claim",
    "recall",
    "expand",
    "briefing",
    "correct",
    "consolidate",
    "resolve_conflicts",
)


def evidence() -> str:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    mcp_version = importlib.metadata.version("mcp")
    turso_version = importlib.metadata.version("pyturso")
    return (
        f"sha {sha}; python {sys.version.split()[0]}; "
        f"mcp {mcp_version}; pyturso {turso_version}"
    )


def assert_no_privileged(rows: list[tuple]) -> None:
    for authority, verify, content in rows:
        if verify:
            raise AssertionError(f"verifier persisted: {verify!r}")
        if authority in {"verified", "human"}:
            raise AssertionError(f"privileged authority write: {authority} content={content!r}")


def _rows(store: MemoryStore) -> list[tuple]:
    cur = store.db.cursor()
    return cur.execute("SELECT authority, verify, content FROM memory_claims").fetchall()


class McpContractTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ.pop("KYPP_DECIDER_URL", None)
        os.environ["KYPP_S1"] = "off"
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(str(Path(self.tmp.name) / "contract.db"))
        self.mcp = build_mcp(self.store, "pillbox", consumer="contract")
        self.evidence = evidence()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _call(self, name: str, arguments: dict) -> str:
        result = asyncio.run(self.mcp.call_tool(name, arguments))
        blocks = result[0] if isinstance(result, tuple) else result
        if isinstance(blocks, dict):
            return str(blocks)
        return "".join(getattr(block, "text", "") or str(block) for block in blocks)

    def test_evidence_names_sha_and_environment(self):
        self.assertRegex(self.evidence, r"sha [0-9a-f]{40}")
        self.assertIn("python ", self.evidence)
        self.assertIn("mcp ", self.evidence)
        self.assertIn("pyturso ", self.evidence)
        print(self.evidence)

    def test_declared_surface_has_no_verifier_param(self):
        tools = asyncio.run(self.mcp.list_tools())
        names = [tool.name for tool in tools]
        self.assertTrue(names)
        self.assertEqual(set(names), set(TOOLS))
        for tool in tools:
            props = (tool.inputSchema or {}).get("properties") or {}
            self.assertNotIn("verify", props, tool.name)
            self.assertNotIn("authority", props, tool.name)

    def test_effective_boundary_ignores_reserved_fields(self):
        succeeded = []
        claim_id = self._call(
            "claim",
            {"subject": "verify wording", "content": "please verify this lesson"},
        )
        succeeded.append("claim")
        self.assertTrue(claim_id.strip())
        self._call("observe", {"content": "please verify the fallback"})
        succeeded.append("observe")
        self._call("recall", {"query": "verify"})
        succeeded.append("recall")
        self._call("expand", {"handle": claim_id.strip()[:8]})
        succeeded.append("expand")
        briefing = self._call("briefing", {})
        succeeded.append("briefing")
        self.assertIsInstance(briefing, str)
        self._call("correct", {"subject": "verify wording", "content": "the right lesson"})
        succeeded.append("correct")
        self._call("consolidate", {"subject": "verify wording"})
        succeeded.append("consolidate")
        self._call("resolve_conflicts", {"subject": "verify wording"})
        succeeded.append("resolve_conflicts")
        self.assertEqual(set(succeeded), set(TOOLS))
        self.assertEqual(len(succeeded), len(TOOLS))
        assert_no_privileged(_rows(self.store))
        stored = [content for _authority, verify, content in _rows(self.store) if "please verify" in (content or "")]
        self.assertTrue(stored)
        self.assertTrue(all(verify is None for _a, verify, _c in _rows(self.store)))

        for name, arguments in (
            ("claim", {"subject": "extra", "content": "kept", "verify": "true", "authority": "verified"}),
            ("claim", {"subject": "human", "content": "kept", "authority": "human"}),
            ("correct", {"subject": "extra", "content": "kept", "verify": "true", "authority": "human"}),
            ("observe", {"content": "please verify", "verify": "true"}),
            ("recall", {"query": "verify", "verify": "true", "authority": "verified"}),
            ("expand", {"handle": claim_id.strip()[:8], "verify": "true"}),
            ("briefing", {"verify": "true", "authority": "verified"}),
            ("consolidate", {"subject": "verify wording", "verify": "true", "authority": "human"}),
            ("resolve_conflicts", {"subject": "verify wording", "verify": "true"}),
        ):
            self._call(name, arguments)
        assert_no_privileged(_rows(self.store))

    def test_forwarded_verifier_fails_the_boundary(self):
        self.store.claim(
            "fact", "bad", "please verify", scope="project", project="pillbox", verify="true"
        )
        with self.assertRaises(AssertionError):
            assert_no_privileged(_rows(self.store))


if __name__ == "__main__":
    unittest.main()
