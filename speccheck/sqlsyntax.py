"""Bounded SQL policy evidence; never executes SQL or proves SQLite acceptance.

Only simple column declarations and selected structural operations are covered.
SQLite's comment/quoting and column-def grammars inform this deliberately smaller
language: https://www.sqlite.org/lang_altertable.html and lang_comment.html.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Analysis:
    kind: str
    reasons: tuple[str, ...]
    version_table: bool = False


_VERSION = {'schema_version', 'schema_versions', 'schema_migrations', 'alembic_version'}
_TYPES = {'TEXT', 'INTEGER', 'INT', 'REAL', 'BLOB', 'NUMERIC', 'BOOLEAN', 'FLOAT', 'DOUBLE', 'VARCHAR'}
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def _lex(sql: str) -> list[list[tuple[str, str]]]:
    if len(sql) > 100_000:
        raise ValueError('SQL exceeds 100000 character bound')
    statements = []
    tokens = []
    depth = 0
    i = 0
    while i < len(sql):
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if sql.startswith('--', i):
            end = sql.find('\n', i + 2)
            i = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            if end < 0:
                raise ValueError('unterminated comment')
            i = end + 2
            continue
        if c in "'\"`[":
            close = ']' if c == '[' else c
            value = ''
            i += 1
            while i < len(sql):
                if sql[i] == close:
                    if close != ']' and i + 1 < len(sql) and sql[i + 1] == close:
                        value += close
                        i += 2
                        continue
                    i += 1
                    break
                value += sql[i]
                i += 1
            else:
                raise ValueError('unterminated quoted token')
            tokens.append(('string' if c == "'" else 'identifier', value))
        elif c == ';':
            if depth:
                raise ValueError('semicolon inside parentheses')
            if tokens:
                statements.append(tokens)
                tokens = []
            i += 1
        elif c in '(),.+-':
            depth += (c == '(') - (c == ')')
            if depth < 0 or depth > 32:
                raise ValueError('unbalanced or deeply nested parentheses')
            tokens.append(('symbol', c))
            i += 1
        else:
            match = _TOKEN.match(sql, i)
            if not match:
                raise ValueError('unsupported token')
            value = match.group()
            tokens.append(('number' if value[0].isdigit() or value[0] == '.' else 'word', value))
            i = match.end()
        if len(tokens) > 4096 or len(statements) > 128:
            raise ValueError('SQL token/statement bound exceeded')
    if depth:
        raise ValueError('unbalanced parentheses')
    if tokens:
        if len(statements) >= 128:
            raise ValueError('SQL token/statement bound exceeded')
        statements.append(tokens)
    return statements


def _word(token: tuple[str, str], value: str) -> bool:
    return token[0] == 'word' and token[1].upper() == value


def _name(tokens: list[tuple[str, str]], i: int) -> tuple[str, int]:
    if i >= len(tokens) or tokens[i][0] not in {'word', 'identifier'}:
        raise ValueError('expected identifier')
    name = tokens[i][1]
    i += 1
    if i < len(tokens) and tokens[i] == ('symbol', '.'):
        i += 1
        if i >= len(tokens) or tokens[i][0] not in {'word', 'identifier'}:
            raise ValueError('expected qualified identifier')
        name = tokens[i][1]
        i += 1
        if i < len(tokens) and tokens[i] == ('symbol', '.'):
            raise ValueError('unsupported qualified identifier')
    return name, i


def _statement(t: list[tuple[str, str]]) -> Analysis:
    def prefix(*words: str) -> bool:
        return len(t) >= len(words) and all(_word(t[i], w) for i, w in enumerate(words))

    if prefix('DROP', 'TABLE') or prefix('DROP', 'INDEX'):
        i = 2
        if len(t) >= 4 and _word(t[2], 'IF') and _word(t[3], 'EXISTS'):
            i = 4
        _, i = _name(t, i)
        if i == len(t):
            return Analysis('not_add_column', (f'DROP {t[1][1].upper()} is non-additive',))
    if prefix('CREATE', 'TABLE') or prefix('CREATE', 'TEMP', 'TABLE') or prefix('CREATE', 'TEMPORARY', 'TABLE'):
        i = 2 if _word(t[1], 'TABLE') else 3
        if len(t) >= i + 3 and all(_word(t[i + j], w) for j, w in enumerate(('IF', 'NOT', 'EXISTS'))):
            i += 3
        name, i = _name(t, i)
        # Require a simple, nonempty column list; complex CREATE forms abstain.
        if i < len(t) and t[i] == ('symbol', '(') and t[-1] == ('symbol', ')'):
            body = t[i + 1:-1]
            parts = [[]]
            for token in body:
                if token == ('symbol', ','):
                    parts.append([])
                else:
                    parts[-1].append(token)
            if all(p and _statement([('word', 'ALTER'), ('word', 'TABLE'), ('word', '_'), ('word', 'ADD'), ('word', 'COLUMN'), *p]).kind in {'ok', 'not_null_without_default'} for p in parts):
                version = name.lower() in _VERSION
                return Analysis('version_table' if version else 'not_add_column', ('CREATE TABLE is non-additive',), version)
    if prefix('ALTER', 'TABLE'):
        _, i = _name(t, 2)
        if i >= len(t):
            raise ValueError('missing ALTER operation')
        if _word(t[i], 'DROP'):
            i += 1
            if i < len(t) and _word(t[i], 'COLUMN'):
                i += 1
            _, i = _name(t, i)
            if i == len(t):
                return Analysis('not_add_column', ('DROP COLUMN is non-additive',))
        elif _word(t[i], 'RENAME'):
            rest = t[i + 1:]
            if len(rest) == 2 and _word(rest[0], 'TO') and rest[1][0] in {'word', 'identifier'}:
                return Analysis('not_add_column', ('RENAME TABLE is non-additive',))
            if rest and _word(rest[0], 'COLUMN'):
                rest = rest[1:]
            if len(rest) == 3 and rest[0][0] in {'word', 'identifier'} and _word(rest[1], 'TO') and rest[2][0] in {'word', 'identifier'}:
                return Analysis('not_add_column', ('RENAME COLUMN is non-additive',))
        elif _word(t[i], 'ADD'):
            i += 1
            if i < len(t) and _word(t[i], 'COLUMN'):
                i += 1
            # Column names cannot be qualified.
            if i >= len(t) or t[i][0] not in {'word', 'identifier'}:
                raise ValueError('missing column name')
            i += 1
            if i >= len(t) or t[i][0] != 'word' or t[i][1].upper() not in _TYPES:
                raise ValueError('unsupported column type')
            i += 1
            not_null = default = False
            while i < len(t):
                if i + 1 < len(t) and _word(t[i], 'NOT') and _word(t[i + 1], 'NULL') and not not_null:
                    not_null = True
                    i += 2
                elif _word(t[i], 'DEFAULT') and not default:
                    default = True
                    i += 1
                    if i < len(t) and t[i] in {('symbol', '+'), ('symbol', '-')}:
                        i += 1
                        if i >= len(t) or t[i][0] != 'number':
                            raise ValueError('expected signed numeric default')
                    if i >= len(t) or not (t[i][0] in {'number', 'string'} or (t[i][0] == 'word' and t[i][1].upper() in {'NULL', 'TRUE', 'FALSE', 'CURRENT_TIME', 'CURRENT_DATE', 'CURRENT_TIMESTAMP'})):
                        raise ValueError('unsupported DEFAULT expression')
                    i += 1
                else:
                    raise ValueError('unsupported or misplaced column clause')
            kind = 'not_null_without_default' if not_null and not default else 'ok'
            return Analysis(kind, ('simple ADD COLUMN declaration; DEFAULT presence only',))
    raise ValueError('unsupported or malformed statement')


def analyze_migration(sql: str) -> Analysis:
    """Return policy evidence, prioritizing counterexamples over statement gaps."""
    try:
        statements = _lex(sql)
    except ValueError as exc:
        return Analysis('unknown', (str(exc),))
    results = []
    for statement in statements:
        try:
            results.append(_statement(statement))
        except ValueError as exc:
            results.append(Analysis('unknown', (str(exc),)))
    if not results:
        return Analysis('unknown', ('no SQL statement',))
    version = any(r.version_table for r in results)
    kinds = {r.kind for r in results}
    kind = next((k for k in ('version_table', 'not_add_column', 'not_null_without_default', 'unknown') if k in kinds), 'ok')
    return Analysis(kind, tuple(reason for r in results for reason in r.reasons), version)
