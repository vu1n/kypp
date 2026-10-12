"""Syntax is cached by content hash. Base-SHA reference rows are not reused."""

import unittest

from speccheck.cache import Cache
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree


class CacheTest(unittest.TestCase):
    def test_syntax_is_content_addressed(self):
        cache = Cache()
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return {"tools": []}

        self.assertEqual(cache.syntax("same", "py-mcp", build)["tools"], [])
        self.assertEqual(cache.syntax("same", "py-mcp", build)["tools"], [])
        cache.syntax("other", "py-mcp", build)
        self.assertEqual(calls["n"], 2)
        self.assertGreaterEqual(cache.syntax_hits, 1)

    def test_base_sha_reference_rows_are_not_reused(self):
        cache = Cache()
        builds = {"n": 0}

        def build():
            builds["n"] += 1
            return {"refs": [builds["n"]]}

        first = cache.references("base", None, "dep", build)
        second = cache.references("head", "base", "dep", build)
        self.assertEqual(first["built_for"], "base")
        self.assertEqual(second["built_for"], "head")
        self.assertGreaterEqual(cache.rejected_base, 1)
        self.assertEqual(cache.ref_builds, 2)
        self.assertEqual(builds["n"], 2)

    def test_dependency_reuse_skips_only_the_base_sha(self):
        cache = Cache()

        def build():
            return {"refs": ["built"]}

        cache.references("other", None, "dep", build)
        reused = cache.references("head", "absent-base", "dep", build)
        self.assertEqual(cache.dependency_reuses, 1)
        self.assertEqual(cache.ref_builds, 1)
        self.assertEqual(reused["built_for"], "other")

    def test_same_sha_hit_requires_matching_dep_hash(self):
        cache = Cache()
        calls = {"n": 0}

        def build():
            calls["n"] += 1
            return {"refs": [calls["n"]]}

        first = cache.references("same", "base", "dep-a", build)
        second = cache.references("same", "base2", "dep-b", build)
        third = cache.references("same", "base2", "dep-b", build)
        self.assertEqual(first["refs"], [1])
        self.assertEqual(second["refs"], [2])
        self.assertEqual(second["built_for"], "same")
        self.assertEqual(third["refs"], [2])
        self.assertEqual(cache.ref_builds, 2)
        self.assertEqual(cache.ref_hits, 1)
        self.assertEqual(calls["n"], 2)

    def test_check_reuses_syntax_and_rebuilds_head_refs(self):
        cache = Cache()
        tree = clean_tree()
        run_check(tree, tree, baseline_sha="base", head_sha="base", cache=cache)
        misses = cache.syntax_misses
        run_check(tree, tree, baseline_sha="base", head_sha="head", cache=cache)
        self.assertGreaterEqual(cache.rejected_base, 1)
        self.assertEqual(cache.ref_builds, 2)
        self.assertGreater(cache.syntax_hits, 0)
        self.assertEqual(cache.syntax_misses, misses)


if __name__ == "__main__":
    unittest.main()
