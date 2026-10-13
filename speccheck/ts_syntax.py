"""Bounded literal TOOLS initializer facts, not a runtime schema evaluator.

Bindings are resolved only at module level. ``const`` does not freeze objects;
extra uses of a referenced schema therefore leave a coverage gap. Parser imports
are lazy so the ordinary kypp installation does not need checker dependencies.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node, Parser


PARSER_VERSION = 'ts-mcp-tree-sitter-v2'


def parser_cache_key() -> str:
    """Probe imports before consulting disk; unavailable parsers never cache facts."""
    from importlib.metadata import version

    _parser()
    return f"{PARSER_VERSION}-{version('tree-sitter')}-{version('tree-sitter-typescript')}"


def _parser() -> Parser:
    from tree_sitter import Language, Parser
    import tree_sitter_typescript

    return Parser(Language(tree_sitter_typescript.language_typescript()))


def _children(node: Node) -> list[Node]:
    return [child for child in node.named_children if child.type != 'comment']


def _unwrap(node: Node | None) -> Node | None:
    while node is not None and node.type in {'parenthesized_expression', 'as_expression', 'satisfies_expression', 'type_assertion'}:
        children = _children(node)
        node = children[-1] if node.type == 'type_assertion' else children[0]
    return node


def _text(node: Node) -> str:
    return node.text.decode('utf8')


def _string(node: Node | None) -> str | None:
    if node is None or node.type != 'string':
        return None
    # JS escapes outside this Python-compatible literal subset are unsupported.
    # In particular legacy octal and identity escapes must not acquire a wrong key.
    raw = _text(node)
    if any(c.type == 'escape_sequence' and not (
        _text(c) in {r'\n', r'\r', r'\t', r'\b', r'\f', r'\v', r'\\', r"\'", r'\"'}
        or (_text(c).startswith((r'\u', r'\x')) and '{' not in _text(c))
    ) for c in node.named_children):
        return None
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _key(node: Node) -> str | None:
    if node.type in {'property_identifier', 'identifier'}:
        value = _text(node)
        return value if '\\' not in value else None
    return _string(node)


def _fields(node: Node | None) -> tuple[dict[str, Node | None], list[str]]:
    """Return unambiguous literal pairs plus gaps; never silently drop a member."""
    node = _unwrap(node)
    if node is None or node.type != 'object':
        return {}, ['expected a literal object']
    fields = {}
    gaps = []
    duplicates = set()
    for child in _children(node):
        if child.type != 'pair':
            gaps.append(f'unsupported object member: {child.type}')
            continue
        key = _key(child.child_by_field_name('key'))
        if key is None:
            gaps.append('nonliteral object key')
            continue
        if key in fields or key in duplicates:
            gaps.append(f'duplicate object key: {key}')
            fields.pop(key, None)
            duplicates.add(key)
        else:
            fields[key] = _unwrap(child.child_by_field_name('value'))
    return fields, gaps


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def typescript_mcp_tools(source: str) -> dict:
    try:
        parser = _parser()
    except (ImportError, OSError, ValueError, TypeError) as exc:
        return {'covered': False, 'error': f'TypeScript parser unavailable; install kypp[speccheck]: {exc}', 'tools': []}
    tree = parser.parse(source.encode('utf8'))
    root = tree.root_node
    if root.has_error:
        return {'covered': False, 'error': 'TypeScript parse error or missing syntax', 'tools': []}
    bindings = {}
    for statement in _children(root):
        if statement.type == 'export_statement':
            statement = statement.child_by_field_name('declaration') or statement
        if statement.type not in {'lexical_declaration', 'variable_declaration'}:
            continue
        for declarator in _children(statement):
            if declarator.type != 'variable_declarator':
                continue
            name = declarator.child_by_field_name('name')
            if name is not None and name.type == 'identifier':
                bindings.setdefault(_text(name), []).append((declarator, statement))
    declarations = bindings.get('TOOLS', [])
    if len(declarations) != 1:
        return {'covered': False, 'error': f'expected one module-level const TOOLS, found {len(declarations)}', 'tools': []}
    declaration, statement = declarations[0]
    array = _unwrap(declaration.child_by_field_name('value'))
    if statement.children[0].type != 'const' or array is None or array.type != 'array':
        return {'covered': False, 'error': 'TOOLS is not a const literal array initializer', 'tools': []}
    gaps = []
    tools = []
    # Named children do not contain elisions: count comma runs using actual tokens.
    previous = '['
    for child in array.children:
        if child.type == 'comment':
            continue
        if child.type == ',' and previous in {'[', ','}:
            gaps.append('TOOLS contains an array elision')
        previous = child.type
    for entry in _children(array):
        fields, entry_gaps = _fields(entry)
        outer_ambiguous = bool(entry_gaps)
        name = _string(fields.get('name'))
        if name is None:
            entry_gaps.append('tool name is not a string literal')
        schema = fields.get('inputSchema')
        if schema is not None and schema.type == 'identifier':
            identifier = _text(schema)
            matches = bindings.get(identifier, [])
            if len(matches) != 1:
                entry_gaps.append(f'inputSchema {identifier} has no unique module binding')
                schema = None
            else:
                binding, owner = matches[0]
                uses = [n for n in _walk(root) if n.type == 'identifier' and _text(n) == identifier]
                permitted = {binding.child_by_field_name('name').id, schema.id}
                # Multiple direct inputSchema uses are also harmless for initializer facts.
                for use in uses:
                    parent = use.parent
                    if parent.type == 'pair' and parent.child_by_field_name('value') == use and _key(parent.child_by_field_name('key')) == 'inputSchema':
                        permitted.add(use.id)
                if owner.children[0].type != 'const' or any(n.id not in permitted for n in uses):
                    entry_gaps.append(f'inputSchema {identifier} may be changed or escape; const is not immutable')
                schema = _unwrap(binding.child_by_field_name('value'))
        schema_fields, schema_gaps = _fields(schema)
        entry_gaps.extend(schema_gaps)
        allowed = {'type', 'properties', 'required', 'additionalProperties', 'description', 'title', '$schema'}
        for key in schema_fields.keys() - allowed:
            entry_gaps.append(f'unsupported schema construct: {key}')
        if 'type' in schema_fields and _string(schema_fields['type']) != 'object':
            entry_gaps.append('schema type is not literal object')
        for key in {'description', 'title', '$schema'} & schema_fields.keys():
            if _string(schema_fields[key]) is None:
                entry_gaps.append(f'dynamic schema metadata: {key}')
        if 'additionalProperties' in schema_fields and schema_fields['additionalProperties'].type != 'false':
            entry_gaps.append('additionalProperties is not false')
        properties, property_gaps = _fields(schema_fields.get('properties'))
        entry_gaps.extend(property_gaps)
        if 'required' in schema_fields:
            required = schema_fields['required']
            names = [_string(n) for n in _children(required)]
            if required.type != 'array' or any(n is None or n not in properties for n in names):
                entry_gaps.append('required is not a literal subset of properties')
        # Ambiguous outer members can replace inputSchema/properties entirely. Do
        # not turn a displaced literal into a counterexample. Property spreads
        # cannot erase a literal key, so those known keys remain useful evidence.
        params = list(properties) if not schema_gaps and not outer_ambiguous else []
        tools.append({'name': name or '?', 'params': params, 'lineno': entry.start_point.row + 1, 'gaps': entry_gaps})
        gaps.extend(f'{name or "?"}: {gap}' for gap in entry_gaps)
    if not tools:
        gaps.append('TOOLS array had no tool objects')
    return {'covered': not gaps, 'error': '; '.join(gaps) or None, 'tools': tools}
