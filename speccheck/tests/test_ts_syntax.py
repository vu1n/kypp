"""Worker signature regressions exercised against the real TypeScript parser."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speccheck.cache import Cache
from speccheck.engine import run_check
from speccheck.fixtures import clean_tree
from speccheck.syntax import cached_ts_mcp, typescript_mcp_tools
from speccheck.ts_syntax import parser_cache_key


def tool(properties='subject: {}', schema=None):
    return '{name: "claim", inputSchema: ' + (schema or '{type: "object", properties: {' + properties + '}}') + '}'


def verdict(source):
    base = clean_tree()
    head = dict(base)
    head['worker/src/mcp.ts'] = source
    report = run_check(base, head, baseline_sha='base', head_sha='head', baseline_paths=sorted(base), head_paths=sorted(head))
    return next(v for v in report.verdicts if v.rule_id == 'mcp-no-verify-argument')


class TypeScriptSyntaxTest(unittest.TestCase):
    def test_unknown_array_member_cannot_conform(self):
        self.assertEqual(verdict('const TOOLS = [' + tool() + ', externalTool];').status, 'unresolved')

    def test_counterexample_survives_unknown_schema(self):
        result = verdict('const TOOLS = [' + tool('verify: {}') + ',' + tool(schema='buildSchema()') + '];')
        self.assertEqual(result.status, 'violates')
        self.assertIn('gaps:', result.coverage)

    def test_quoted_keys_and_comments(self):
        source = '// const TOOLS = [fake]\nconst text = "properties: { verify: {} }";\nconst TOOLS = [' + tool('"verify": {}, /* } ] */ subject: {}') + '];'
        self.assertEqual(verdict(source).status, 'violates')

    def test_unknown_constructs_abstain(self):
        sources = [
            'const TOOLS = [' + tool() + ', ...others];',
            'const TOOLS = [' + tool() + ',,];',
            'const TOOLS = [' + tool('[key]: {}') + '];',
            'const TOOLS = [' + tool('...fields') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, allOf: [other]}') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, properties: dynamic}') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, ...other}') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, additionalProperties: true}') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, required: ["verify"]}') + '];',
            'const TOOLS = [' + tool() + ']; const TOOLS = [];',
            'const TOOLS = [' + tool() + ']; !!!',
            'const TOOLS = [' + tool('get verify() {return {}}') + '];',
            'const TOOLS = [' + tool(schema='{properties: {}, patternProperties: dynamic}') + '];',
            'const TOOLS = [' + tool(schema='{properties: fields}') + '];',
            'const TOOLS = [' + tool(schema='schema') + ']; const schema = {properties: {}}; const schema = {};',
            'const TOOLS = [' + tool(schema='schema') + ']; let schema = {properties: {}};',
            'const TOOLS = [' + tool(schema='{properties: {}, required: dynamic}') + '];',
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertNotEqual(verdict(source).status, 'conforms')

    def test_literal_reference_is_syntax_evidence_only(self):
        source = 'const schema = {properties: {verify: {}}}; const TOOLS = [' + tool(schema='schema') + '];'
        self.assertEqual(verdict(source).status, 'violates')
        clean = source.replace('verify', 'subject')
        self.assertEqual(verdict(clean).status, 'conforms')
        self.assertIn('literal', verdict(clean).detail)
        changed = clean + ' schema.properties.verify = {};'
        self.assertEqual(verdict(changed).status, 'unresolved')

    def test_escaped_quoted_key(self):
        source = 'const TOOLS = [' + tool(r'"ver\u0069fy": {}') + '];'
        self.assertEqual(verdict(source).status, 'violates')

    def test_schema_passed_outside_tools_is_an_escape(self):
        source = 'const schema = {properties: {subject: {}}}; configure({inputSchema: schema}); const TOOLS = [' + tool(schema='schema') + '];'
        self.assertEqual(verdict(source).status, 'unresolved')

    def test_schema_shorthand_and_escaped_identifiers_are_escapes(self):
        prefix = 'const schema = {properties: {subject: {}}}; '
        suffix = ' const TOOLS: unknown[] = [' + tool(schema='schema') + '];'
        for use in ('configure({schema});', r'configure(sch\u0065ma);'):
            with self.subTest(use=use):
                self.assertEqual(verdict(prefix + use + suffix).status, 'unresolved')

    def test_dependency_version_changes_reject_disk_facts(self):
        source = 'const TOOLS = [' + tool() + ', externalTool];'
        old_versions = {'tree-sitter': 'old-runtime', 'tree-sitter-typescript': 'old-grammar'}
        for package in old_versions:
            with self.subTest(package=package), tempfile.TemporaryDirectory() as directory:
                cache = Cache(Path(directory))
                with patch('importlib.metadata.version', side_effect=old_versions.__getitem__):
                    kind = parser_cache_key()
                    cache.syntax(source, kind, lambda: {'covered': True, 'error': None, 'tools': []})
                new_versions = {**old_versions, package: 'new-version'}
                with patch('importlib.metadata.version', side_effect=new_versions.__getitem__):
                    fresh = Cache(Path(directory))
                    facts = cached_ts_mcp(fresh, source)
                self.assertFalse(facts['covered'])
                self.assertEqual(fresh.syntax_hits, 0)

    def test_previous_reference_whitelist_cache_is_rejected(self):
        source = 'const schema = {properties: {subject: {}}}; configure({inputSchema: schema}); const TOOLS = [' + tool(schema='schema') + '];'
        for old_version in ('ts-mcp-tree-sitter-v2', 'ts-mcp-tree-sitter-v3'):
            with self.subTest(version=old_version), tempfile.TemporaryDirectory() as directory:
                cache = Cache(Path(directory))
                with patch('speccheck.ts_syntax.PARSER_VERSION', old_version):
                    kind = parser_cache_key()
                    cache.syntax(source, kind, lambda: {'covered': True, 'error': None, 'tools': []})
                fresh = Cache(Path(directory))
                self.assertFalse(cached_ts_mcp(fresh, source)['covered'])
                self.assertEqual(fresh.syntax_hits, 0)

    def test_nested_decoys_do_not_supply_properties(self):
        source = 'const TOOLS = [' + tool(schema='{description: "properties: {subject: {}}", nested: {properties: {subject: {}}}}') + '];'
        self.assertEqual(verdict(source).status, 'unresolved')

    def test_wrappers_and_line_numbers(self):
        source = '\nconst TOOLS: unknown[] = ([(' + tool('verify: {}') + ') as const]) satisfies unknown[];'
        facts = typescript_mcp_tools(source)
        self.assertTrue(facts['covered'], facts)
        self.assertEqual(facts['tools'][0]['lineno'], 2)
        self.assertEqual(verdict(source).status, 'violates')

    def test_missing_dependencies_abstain_without_poisoning_cache(self):
        source = 'const TOOLS = [' + tool() + '];'
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            with patch('speccheck.ts_syntax._parser', side_effect=ImportError('missing tree-sitter')):
                facts = cached_ts_mcp(cache, source)
                self.assertFalse(facts['covered'])
                self.assertIn('install kypp[speccheck]', facts['error'])
            self.assertTrue(cached_ts_mcp(cache, source)['covered'])
            self.assertEqual(cache.syntax_hits, 0)

    def test_partial_property_keys_survive_gap(self):
        self.assertEqual(verdict('const TOOLS = [' + tool('verify: {}, ...fields') + '];').status, 'violates')

    def test_fake_literals_do_not_supply_tools(self):
        source = 'const text = `const TOOLS = [{name: "fake", inputSchema: {properties: {}}}]`;'
        self.assertFalse(typescript_mcp_tools(source)['covered'])

    def test_old_disk_cache_is_rejected(self):
        source = 'const TOOLS = [' + tool() + ', unknown];'
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            cache.syntax(source, 'ts-mcp', lambda: {'covered': True, 'error': None, 'tools': []})
            fresh = Cache(Path(directory))
            self.assertFalse(cached_ts_mcp(fresh, source)['covered'])
            self.assertEqual(fresh.syntax_hits, 0)


if __name__ == '__main__':
    unittest.main()
