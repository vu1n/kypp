"""One contract, three adapters: turso (existing files), libsql (FTS5 + vectors), sqlite FTS5.

Semantic behavior follows `capabilities`. A backend without vectors does not drop
keyword dedup, and it does not pretend the vector pass ran.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kypp.arbiter import consolidate
from kypp.backends import LibsqlStore, SqliteFtsStore
from kypp.repository import SemanticUnavailable, index_context_sources
from kypp.store import MemoryStore

REF = "doc://kypp/authority-order@0006#authority-dominates"
SOURCE = (
    "# Context: doc://kypp/authority-order@0006#authority-dominates"
    " — only mark_verified grants verified.\n"
)


class _GoneResolver:
    """Every anchor under gone/ is unresolved; anything else resolves."""

    available = True

    def resolve(self, ref: dict) -> dict:
        return {"status": "unresolved" if ref["path"].startswith("gone/") else "grounded"}


def _embed(text: str) -> list[float]:
    """Query 'ftsmarker' sits next to the paraphrase and away from the lexical claim.

    alpha/beta are a separate near-duplicate pair for find_similar. They are not close
    to either of the search claims.
    """
    if text.strip() == "ftsmarker":
        return [1.0, 0.0, 0.0]
    if "paraphrase" in text:
        return [1.0, 0.0, 0.05]
    if "ftsmarker" in text:
        return [0.0, 1.0, 0.0]
    if "alpha" in text:
        return [0.0, 0.0, 1.0]
    if "beta" in text:
        return [0.0, 0.02, 1.0]
    return [0.3, 0.3, 0.3]


class StoreContract:
    """Shared cases. Not a TestCase itself — unittest would try to run `open`."""

    semantic = False
    fts = False

    def open(self, path: str, embed=None):
        raise NotImplementedError

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "memory.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_put_get_and_keyword_search(self):
        store = self.open(self.path)
        self.assertEqual(store.capabilities.semantic, self.semantic)
        self.assertEqual(store.capabilities.fts, self.fts)
        self.assertTrue(store.capabilities.pointers)
        cid = store.put("fact", "libkrun rebuild", "codesign after a plain build", scope="project",
                        project="pillbox", accept=True)
        got = store.get(cid[:8])
        self.assertEqual(got.id, cid)
        hits = store.search("codesign", project="pillbox")
        self.assertEqual([c.id for c in hits], [cid])

    def test_stale_claims_rank_after_live_ones_unless_named(self):
        store = self.open(self.path)
        store.resolver = _GoneResolver()
        store.put("pitfall", "live code", "codesign lesson", scope="project", project="p", accept=True,
                  code_refs=[{"symbol": "live_fn", "path": "src/live.rs", "query": "live_fn"}])
        # Written later, so it would otherwise rank first.
        store.put("pitfall", "gone code", "codesign lesson", scope="project", project="p", accept=True,
                  code_refs=[{"symbol": "gone_fn", "path": "gone/old.rs", "query": "gone_fn"}])
        hits = store.search("codesign lesson", project="p")
        self.assertEqual([c.subject for c in hits], ["live code", "gone code"])
        self.assertTrue(hits[1].stale)
        named = store.search("codesign lesson gone/old.rs", project="p")
        self.assertEqual(named[0].subject, "gone code")
        browse = store.recall("", project="p")
        self.assertEqual([c.subject for c in browse], ["live code", "gone code"])

    def test_rejected_claim_stays_readable_and_leaves_search(self):
        store = self.open(self.path)
        cid = store.put("fact", "bad lesson", "drop this from recall", scope="project",
                        project="p", accept=True)
        store.reject(cid, reason="wrong")
        self.assertEqual(store.get(cid).status, "rejected")
        self.assertEqual(store.search("drop this from recall", project="p"), [])

    def test_visibility_and_status_filter_before_text_and_pointer_limits(self):
        store = self.open(self.path)
        for project, status in [("elsewhere", "accepted"), ("mine", "candidate"), ("mine", "rejected")]:
            for i in range(30):
                cid = store.put("fact", f"other {i}", "needle", scope="project", project=project,
                                accept=status == "accepted", code_refs=[{"path": "target.py"}])
                if status == "rejected":
                    store.reject(cid)
        wanted = store.put("fact", "wanted", "needle is in a longer and lower-scoring sentence", scope="project",
                           project="mine", accept=True, code_refs=[{"path": "target.py"}])
        self.assertEqual([c.id for c in store.recall("needle", project="mine", limit=1)], [wanted])
        self.assertEqual([c.id for c in store.recall("target.py", project="mine", limit=1)], [wanted])

    def test_unicode_words_and_punctuation_are_not_accidental_browse(self):
        store = self.open(self.path)
        cid = store.put("fact", "café", "日本語 and café", scope="project", project="p", accept=True)
        for query in ["café", "日本語"] + (["cafe", "cafe\u0301"] if self.fts else []):
            self.assertEqual([c.id for c in store.search(query, project="p")], [cid], query)
        self.assertEqual(store.search("🚀 !!", project="p"), [])

    def test_fts_backfills_existing_rows_once(self):
        if not self.fts:
            return
        store = self.open(self.path)
        cid = store.put("fact", "existing", "before the FTS adapter", scope="project", project="p", accept=True)
        # Simulate a row predating the index, without deleting any claim/history.
        store._write([("DELETE FROM claims_fts WHERE claim_id=?", (cid,))])
        reopened = self.open(self.path)
        self.assertEqual([c.id for c in reopened.search("existing", project="p")], [cid])
        reopened_again = self.open(self.path)
        n = reopened_again.db.cursor().execute("SELECT COUNT(*) FROM claims_fts WHERE claim_id=?", (cid,)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_pointers_resolve_both_ways_and_rank_search(self):
        store = self.open(self.path)
        cid = store.put(
            "fact", "verified writer", "the check grants verified authority",
            scope="project", project="p", accept=True,
            code_refs=[{"symbol": "mark_verified", "path": "kypp/store.py"}],
        )
        # The text does not contain the path or the anchor. Ranking still surfaces the claim.
        store.link_pointer(REF, "kypp/store.py", symbol="mark_verified", claim_id=cid, kind="context")
        by_path = store.entries_for_path("kypp/store.py")
        self.assertTrue(any(p.entry_ref == REF and p.claim_id == cid for p in by_path))
        self.assertTrue(any(p.kind == "code_ref" and p.symbol == "mark_verified" for p in by_path))
        by_entry = store.paths_for_entry(REF)
        self.assertEqual({p.path for p in by_entry}, {"kypp/store.py"})
        # A different revision of the same anchor resolves to the same file.
        self.assertTrue(store.paths_for_entry("doc://kypp/authority-order@0008#authority-dominates"))
        self.assertTrue(store.paths_for_entry("authority-dominates"))
        ranked = store.search("kypp/store.py", project="p")
        self.assertTrue(ranked and ranked[0].id == cid, [c.subject for c in ranked])
        by_anchor = store.search("authority-dominates", project="p")
        self.assertTrue(by_anchor and by_anchor[0].id == cid)
        cols = [row[1] for row in store.db.cursor().execute("PRAGMA table_info(code_pointers)").fetchall()]
        self.assertNotIn("line", cols)

    def test_context_comments_index_without_a_claim(self):
        store = self.open(self.path)
        n = index_context_sources(store, {"speccheck/syntax.py": SOURCE})
        self.assertEqual(n, 1)
        entries = store.entries_for_path("speccheck/syntax.py")
        self.assertEqual([p.entry_ref for p in entries], [REF])
        self.assertEqual(entries[0].claim_id, "")
        paths = store.paths_for_entry("authority-dominates")
        self.assertEqual([p.path for p in paths], ["speccheck/syntax.py"])

    def test_full_pointer_ref_does_not_alias_another_project_or_document(self):
        store = self.open(self.path)
        # Foreign fixture identities, not Context bindings to this repo's decisions.
        def ref(project, document, revision):
            return f"doc://{project}/{document}@{revision}#shared"
        for project, document, path in [("a", "one", "right.py"), ("b", "one", "other-project.py"),
                                        ("a", "two", "other-doc.py")]:
            store.link_pointer(ref(project, document, "0001"), path)
        self.assertEqual([p.path for p in store.paths_for_entry(ref("a", "one", "0002"))], ["right.py"])
        self.assertEqual(len(store.paths_for_entry("shared")), 3)

    def test_semantic_capability(self):
        store = self.open(self.path, embed=_embed)
        store.put("fact", "lexical", "the ftsmarker is a rare token", scope="project",
                  project="p", accept=True)
        store.put("fact", "paraphrase", "a paraphrase of the same lesson", scope="project",
                  project="p", accept=True)
        store.put("fact", "alpha lesson", "near duplicate alpha", scope="project", project="p", accept=True)
        store.put("fact", "beta lesson", "near duplicate beta", scope="project", project="p", accept=True)
        hits = {c.subject for c in store.search("ftsmarker", project="p", limit=10)}
        self.assertIn("lexical", hits)
        if self.semantic:
            self.assertIn("paraphrase", hits)
            pairs = store.find_similar("p", max_distance=0.2)
            subjects = set()
            for a, b in pairs:
                subjects.add(store.get(a).subject)
                subjects.add(store.get(b).subject)
            self.assertEqual(subjects, {"alpha lesson", "beta lesson"})
        else:
            self.assertNotIn("paraphrase", hits)
            with self.assertRaises(SemanticUnavailable):
                store.find_similar("p", max_distance=0.2)
            store.put("fact", "same subject", "one", scope="project", project="p", accept=True)
            store.put("fact", "same subject", "two", scope="project", project="p", accept=True)
            result = consolidate(store, project="p", semantic=0.15)
            self.assertIn("unavailable", result["semantic"])
            self.assertGreaterEqual(result["superseded"], 1)
            live = [c.content for c in store.live_claims("p", "same subject")]
            self.assertEqual(len(live), 1)


class TursoContract(StoreContract, unittest.TestCase):
    semantic = True
    fts = False

    def open(self, path, embed=None):
        return MemoryStore(path, embed=embed)


class LibsqlContract(StoreContract, unittest.TestCase):
    semantic = True
    fts = True

    def open(self, path, embed=None):
        return LibsqlStore(path, embed=embed)


class SqliteFtsContract(StoreContract, unittest.TestCase):
    semantic = False
    fts = True

    def open(self, path, embed=None):
        return SqliteFtsStore(path, embed=embed)


if __name__ == "__main__":
    unittest.main()
