"""Constructing the server imports FastMCP. Importing kypp.mcp_server does not."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kypp.mcp_server import build_mcp
from kypp.store import MemoryStore


class McpConstructTest(unittest.TestCase):
    def test_build_mcp_constructs_a_server(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = build_mcp(MemoryStore(str(Path(tmp.name) / "smoke.db")), "pillbox")
        self.assertIsNotNone(server)
        self.assertTrue(callable(getattr(server, "list_tools", None)))
