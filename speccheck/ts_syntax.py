"""Bounded literal TOOLS initializer facts, not a runtime schema evaluator.

Bindings are resolved only at module level. ``const`` does not freeze objects;
extra uses of a referenced schema therefore leave a coverage gap. Parser imports
are lazy so the ordinary kypp installation does not need checker dependencies.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node, Parser


PARSER_VERSION = 'ts-mcp-tree-sitter-v6'


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
    literal_tool_ids = set()
    for entry in _children(array):
        literal = _unwrap(entry)
        if literal is not None and literal.type == 'object':
            literal_tool_ids.add(literal.id)
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
            matches = bindings.get(identifier, []) if '\\' not in identifier else []
            if len(matches) != 1:
                entry_gaps.append(f'inputSchema {identifier} has no unique module binding')
                schema = None
            else:
                binding, owner = matches[0]
                reference_types = {'identifier', 'shorthand_property_identifier', 'shorthand_property_identifier_pattern'}
                # Escaped spellings may name this binding; abstain rather than
                # treating raw-text inequality as evidence of a different name.
                uses = [n for n in _walk(root) if n.type in reference_types
                        and (_text(n) == identifier or '\\' in _text(n))]
                permitted = {binding.child_by_field_name('name').id, schema.id}
                # Only direct schema positions in this TOOLS initializer are harmless.
                # A similarly named pair in an opaque call can expose the same object.
                for use in uses:
                    parent = use.parent
                    if (parent.type == 'pair' and parent.parent.id in literal_tool_ids
                        and parent.child_by_field_name('value') == use
                        and _key(parent.child_by_field_name('key')) == 'inputSchema'):
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

def _match_delim(src: str, start: int, open_c: str, close_c: str) -> int | None:
    depth = 0
    i = start
    n = len(src)
    state = "code"
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "line":
            if c == "\n":
                state = "code"
            i += 1
            continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue
        if state in {"sq", "dq", "bt"}:
            if c == "\\":
                i += 2
                continue
            quote = {"sq": "'", "dq": '"', "bt": "`"}[state]
            if c == quote:
                state = "code"
            i += 1
            continue
        if c == "/" and nxt == "/":
            state = "line"
            i += 2
            continue
        if c == "/" and nxt == "*":
            state = "block"
            i += 2
            continue
        if c == "'":
            state = "sq"
            i += 1
            continue
        if c == '"':
            state = "dq"
            i += 1
            continue
        if c == "`":
            state = "bt"
            i += 1
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None



def _strip_ts_comments(source: str) -> str:
    out: list[str] = []
    i = 0
    n = len(source)
    state = "code"
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if state == "line":
            if c == "\n":
                state = "code"
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if state in {"sq", "dq", "bt"}:
            out.append(c)
            if c == "\\":
                if i + 1 < n:
                    out.append(source[i + 1])
                i += 2
                continue
            if c == {"sq": "'", "dq": '"', "bt": "`"}[state]:
                state = "code"
            i += 1
            continue
        if c == "/" and nxt == "/":
            state = "line"
            out.append("  ")
            i += 2
            continue
        if c == "/" and nxt == "*":
            state = "block"
            out.append("  ")
            i += 2
            continue
        if c in {"'", '"', "`"}:
            state = {"'": "sq", '"': "dq", "`": "bt"}[c]
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)



def _split_top(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    close_for: dict[str, str] = {}
    while i < len(text):
        c = text[i]
        if c in pairs:
            depth += 1
            close_for[str(depth)] = pairs[c]
            buf.append(c)
        elif depth and c == close_for.get(str(depth)):
            depth -= 1
            buf.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _unwrap_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and _match_delim(expr, 0, "(", ")") == len(expr) - 1:
        expr = expr[1:-1].strip()
    return expr


def _ts_rank_polarity(expr: str) -> str:
    expr = _unwrap_parens(expr)
    if expr.startswith("+") and not expr.startswith("+="):
        return _ts_rank_polarity(expr[1:])
    if expr.startswith("-"):
        inner = _ts_rank_polarity(expr[1:])
        return {"ascending": "flipped", "flipped": "ascending"}.get(inner, "unknown" if inner == "unknown" else "absent")
    compact = re.sub(r"\s+", "", expr)
    if re.fullmatch(r"AUTHORITIES\.length(?:-1)?-AUTHORITIES\.indexOf\(.*\)", compact):
        return "flipped"
    if "AUTHORITIES" in compact and ".reverse()" in compact and ".indexOf(" in compact:
        return "flipped"
    if re.fullmatch(r"AUTHORITIES\.indexOf\(.*\)", compact):
        return "ascending"
    if "AUTHORITIES" in compact:
        return "unknown"
    return "absent"


def _ts_return_first(body: str) -> str | None:
    match = re.search(r"\breturn\b", body)
    if not match:
        return None
    rest = body[match.end() :].lstrip()
    if rest.startswith("["):
        end = _match_delim(rest, 0, "[", "]")
        if end is None:
            return None
        return _split_top(rest[1:end], ",")[0]
    if rest.startswith("("):
        end = _match_delim(rest, 0, "(", ")")
        if end is None:
            return None
        return _split_top(rest[1:end], ",")[0]
    return None


def _ts_string_array(source: str, name: str) -> list[str] | None:
    match = re.search(rf"\b{name}\b(?:\s*:\s*[^=;\n]+)?\s*=\s*\[", source)
    if not match:
        return None
    end = _match_delim(source, match.end() - 1, "[", "]")
    if end is None:
        return None
    body = source[match.end() : end]
    values = re.findall(r"""['"]([^'"]+)['"]""", body)
    if not values or re.search(r"\.\.\.|\b\w+\s*\(", body):
        return None
    return values


def _direct_rank_arg(value) -> str | None:
    """The parameter name when ``value`` is exactly ``rank(<identifier>)``."""
    value = _unwrap(value)
    if value is None or value.type != "call_expression":
        return None
    func = value.child_by_field_name("function")
    if func is None or func.type != "identifier" or _text(func) != "rank":
        return None
    args = value.child_by_field_name("arguments")
    if args is None:
        return None
    got = list(args.named_children)
    if len(got) != 1 or got[0].type != "identifier":
        return None
    return _text(got[0])


def _returned_int(stmt) -> int | None:
    kids = [child for child in stmt.named_children]
    if len(kids) != 1:
        return None
    node = _unwrap(kids[0])
    if node is None:
        return None
    if node.type == "number" and _text(node).isdigit():
        return int(_text(node))
    if node.type == "unary_expression":
        op = next((child.text.decode("utf8") for child in node.children if not child.is_named), "")
        nums = [child for child in node.named_children if child.type == "number"]
        if len(nums) == 1 and _text(nums[0]).isdigit() and op in {"-", "+"}:
            value = int(_text(nums[0]))
            return value if op == "+" else -value
    return None


def _if_return(node) -> int | None:
    if node.type == "return_statement":
        return _returned_int(node)
    if node.type == "statement_block":
        stmts = [child for child in node.named_children if child.type != "comment"]
        if len(stmts) == 1 and stmts[0].type == "return_statement":
            return _returned_int(stmts[0])
    return None


def _subscript_pair(expr):
    expr = _unwrap(expr)
    if expr is None or expr.type != "binary_expression":
        return None
    op = next((child.text.decode("utf8") for child in expr.children if not child.is_named), "")
    if op not in {"<", ">"}:
        return None
    sides = [child for child in expr.named_children if child.type == "subscript_expression"]
    if len(sides) != 2:
        return None
    names = []
    indexes = []
    for side in sides:
        obj = side.child_by_field_name("object")
        index = side.child_by_field_name("index")
        if obj is None or obj.type != "identifier" or index is None:
            return None
        names.append(_text(obj))
        indexes.append(_text(index))
    if indexes[0] != indexes[1]:
        return None
    return names[0], names[1], op


def _block_stmts(node):
    node = _unwrap(node)
    if node is None or node.type != "statement_block":
        return None
    return [child for child in node.named_children if child.type != "comment"]


def _binding_decls(stmt) -> list[tuple[str, str]] | None:
    """Every declarator must be exactly ``rank(<identifier>)``."""
    if stmt.type != "lexical_declaration":
        return None
    decls = [child for child in stmt.named_children if child.type == "variable_declarator"]
    if not decls:
        return None
    found: list[tuple[str, str]] = []
    for decl in decls:
        ident = decl.child_by_field_name("name")
        if ident is None or ident.type != "identifier":
            return None
        arg = _direct_rank_arg(decl.child_by_field_name("value"))
        if arg is None:
            return None
        found.append((_text(ident), arg))
    return found


def _loop_index(init) -> str | None:
    if init is None or init.type != "lexical_declaration":
        return None
    decls = [child for child in init.named_children if child.type == "variable_declarator"]
    if len(decls) != 1:
        return None
    ident = decls[0].child_by_field_name("name")
    value = _unwrap(decls[0].child_by_field_name("value"))
    if ident is None or ident.type != "identifier" or value is None:
        return None
    if value.type != "number" or _text(value) != "0":
        return None
    return _text(ident)


def _anon_op(node) -> str:
    return next((child.text.decode("utf8") for child in node.children if not child.is_named), "")


def _length_test(cond, var: str, left: str) -> bool:
    cond = _unwrap(cond)
    if cond is None or cond.type != "binary_expression" or _anon_op(cond) != "<":
        return False
    sides = list(cond.named_children)
    if len(sides) != 2 or sides[0].type != "identifier" or _text(sides[0]) != var:
        return False
    member = _unwrap(sides[1])
    if member is None or member.type != "member_expression":
        return False
    obj = member.child_by_field_name("object")
    prop = member.child_by_field_name("property")
    return (
        obj is not None
        and obj.type == "identifier"
        and _text(obj) == left
        and prop is not None
        and _text(prop) == "length"
    )


def _is_increment(inc, var: str) -> bool:
    if inc is None or inc.type != "update_expression" or _anon_op(inc) != "++":
        return False
    names = [child for child in inc.named_children if child.type == "identifier"]
    return len(names) == 1 and _text(names[0]) == var


def _if_polarity(ifs, left: str, right: str, var: str) -> str | None:
    if len(ifs) != 2:
        return None
    less = greater = None
    for node in ifs:
        if node.type != "if_statement" or node.child_by_field_name("alternative") is not None:
            return None
        pair = _subscript_pair(_unwrap(node.child_by_field_name("condition")))
        returned = _if_return(node.child_by_field_name("consequence"))
        if pair is None or returned not in {-1, 1}:
            return None
        got_left, got_right, op = pair
        indexes = _subscript_indexes(node.child_by_field_name("condition"))
        if indexes != (var, var) or got_left != left or got_right != right:
            return None
        if op == "<":
            if less is not None:
                return None
            less = returned
        elif op == ">":
            if greater is not None:
                return None
            greater = returned
    if less == -1 and greater == 1:
        return "natural"
    if less == 1 and greater == -1:
        return "inverted"
    return None


def _subscript_indexes(expr) -> tuple[str, str] | None:
    expr = _unwrap(expr)
    if expr is None or expr.type != "binary_expression":
        return None
    sides = [child for child in expr.named_children if child.type == "subscript_expression"]
    if len(sides) != 2:
        return None
    indexes = []
    for side in sides:
        index = side.child_by_field_name("index")
        if index is None:
            return None
        indexes.append(_text(index))
    return indexes[0], indexes[1]


def _for_polarity(stmt, left: str, right: str) -> str | None:
    if stmt.type != "for_statement":
        return None
    var = _loop_index(stmt.child_by_field_name("initializer"))
    if var is None:
        return None
    if not _length_test(stmt.child_by_field_name("condition"), var, left):
        return None
    if not _is_increment(stmt.child_by_field_name("increment"), var):
        return None
    body = _block_stmts(stmt.child_by_field_name("body"))
    if body is None:
        return None
    return _if_polarity(body, left, right, var)


def _ts_comparator(fn) -> str:
    """natural/inverted only for the one proved compareRank shape.

    Bindings are ``rank`` of the two parameters, then one index loop whose first
    discriminator is that pair, then ``return 0``. Any other control flow, including
    an earlier return that ignores the ranks, is unknown.
    """
    if fn is None:
        return "unknown"
    params = fn.child_by_field_name("parameters")
    body = fn.child_by_field_name("body")
    if params is None or body is None:
        return "unknown"
    names: list[str] = []
    for child in params.named_children:
        if child.type not in {"required_parameter", "optional_parameter"}:
            return "unknown"
        pattern = child.child_by_field_name("pattern")
        if pattern is None or pattern.type != "identifier":
            return "unknown"
        names.append(_text(pattern))
    if len(names) != 2:
        return "unknown"
    stmts = _block_stmts(body)
    if not stmts:
        return "unknown"
    bindings: list[tuple[str, str]] = []
    index = 0
    while index < len(stmts) and stmts[index].type == "lexical_declaration":
        got = _binding_decls(stmts[index])
        if got is None:
            return "unknown"
        bindings.extend(got)
        index += 1
        if len(bindings) >= 2:
            break
    if len(bindings) != 2 or len({name for name, _arg in bindings}) != 2:
        return "unknown"
    by_param = {arg: name for name, arg in bindings}
    if set(by_param) != set(names):
        return "unknown"
    if index >= len(stmts):
        return "unknown"
    polarity = _for_polarity(stmts[index], by_param[names[0]], by_param[names[1]])
    if polarity is None or index != len(stmts) - 2:
        return "unknown"
    last = stmts[-1]
    if last.type != "return_statement" or _returned_int(last) != 0:
        return "unknown"
    return polarity


def _ts_survivor_picks(body: str, comparator: str) -> str:
    """with_rank if the greater rank number wins; against_rank if the lesser wins; else unknown."""
    flat = re.sub(r"\s+", " ", body)
    match = re.search(
        r"compareRank\s*\(\s*([A-Za-z_$][\w$]*)\s*,\s*([A-Za-z_$][\w$]*)\s*\)\s*(>=|>|<=|<)\s*0\s*\?\s*([A-Za-z_$][\w$]*)\s*:\s*([A-Za-z_$][\w$]*)",
        flat,
    )
    if match is None or comparator == "unknown":
        return "unknown"
    first, _second, op, kept, _other = match.groups()
    keeps_first = kept == first
    greater_op = op in {">", ">="}
    # natural comparator: positive means the first argument ranks higher.
    first_is_greater = (greater_op and keeps_first) or (not greater_op and not keeps_first)
    if comparator == "inverted":
        first_is_greater = not first_is_greater
    return "with_rank" if first_is_greater else "against_rank"




def _function_decl(root, name: str):
    for node in _walk(root):
        if node.type != "function_declaration":
            continue
        ident = node.child_by_field_name("name")
        if ident is not None and _text(ident) == name:
            return node
    return None


def _function_body(root, name: str) -> str | None:
    node = _function_decl(root, name)
    if node is None:
        return None
    body = node.child_by_field_name("body")
    return None if body is None else _text(body)


def _authorities_array(root) -> list[str] | None:
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        ident = node.child_by_field_name("name")
        if ident is None or _text(ident) != "AUTHORITIES":
            continue
        value = _unwrap(node.child_by_field_name("value"))
        if value is None or value.type != "array":
            return None
        out = []
        for child in _children(value):
            text = _string(child)
            if text is None:
                return None
            out.append(text)
        return out or None
    return None


def typescript_authority_rank(source: str) -> dict:
    """Effective low-to-high authority order in worker/src/memory.ts, or unknown."""
    try:
        parser = _parser()
    except (ImportError, OSError, ValueError, TypeError) as exc:
        return {"covered": True, "order": "unknown", "error": f"TypeScript parser unavailable: {exc}"}
    tree = parser.parse(source.encode("utf8"))
    root = tree.root_node
    if root.has_error:
        return {"covered": True, "order": "unknown", "error": "TypeScript parse error"}
    array = _authorities_array(root)
    rank_body = _function_body(root, "rank")
    compare = _function_decl(root, "compareRank")
    survivor_body = _function_body(root, "survivor")
    if array is None or rank_body is None:
        return {"covered": True, "order": "unknown", "error": "AUTHORITIES or rank() was not recognized"}
    rank_body = _strip_ts_comments(rank_body)
    first = _ts_return_first(rank_body)
    polarity = _ts_rank_polarity(first or "")
    if polarity in {"unknown", "absent"}:
        return {"covered": True, "order": polarity, "error": None}
    if compare is None or survivor_body is None:
        return {"covered": True, "order": "unknown", "error": "compareRank or survivor was not recognized"}
    direction = _ts_survivor_picks(_strip_ts_comments(survivor_body), _ts_comparator(compare))
    if direction == "unknown":
        return {"covered": True, "order": "unknown", "error": "survivor direction was not proved"}
    order = list(array)
    if polarity == "flipped":
        order.reverse()
    if direction == "against_rank":
        order.reverse()
    return {"covered": True, "order": order, "error": None}


def _note_sql(text: str | None, fragments: str, dynamic: bool, where: str) -> tuple[list[str], list[str]]:
    from .syntax import sql_write_flags

    bad: list[str] = []
    gaps: list[str] = []
    shown = text if text is not None else fragments
    if not shown or not any(ch.isalnum() for ch in shown):
        return bad, gaps
    delete_bad, delete_gap, verified_bad, verified_gap = sql_write_flags(shown, dynamic)
    if delete_bad:
        bad.append(f"DELETE {where}")
    elif delete_gap:
        gaps.append(f"dynamic DELETE {where}")
    if verified_bad:
        bad.append(f"verified SQL {where}")
    elif verified_gap:
        gaps.append(f"dynamic authority SQL {where}")
    return bad, gaps


def _sql_file(path: str, source: str) -> tuple[list[str], list[str]]:
    cleaned = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    cleaned = re.sub(r"--.*?$", " ", cleaned, flags=re.M)
    return _note_sql(cleaned, cleaned, False, f"{path}:1")


_FOLD_CONTAINERS = {
    "binary_expression",
    "template_string",
    "call_expression",
    "array",
    "arguments",
    "parenthesized_expression",
    "member_expression",
    "as_expression",
}


def _ts_fold_sql(node) -> tuple[str | None, str, bool] | None:
    """(full string or None, fragments with \\x00 holes, dynamic). None if this is not a string expression."""
    node = _unwrap(node)
    if node is None:
        return None
    if node.type == "string":
        text = _string(node)
        if text is None:
            raw = _text(node)
            return None, raw, True
        return text, text, False
    if node.type == "template_string":
        parts: list[str] = []
        dynamic = False
        for child in node.named_children:
            if child.type == "string_fragment":
                parts.append(_text(child))
                continue
            if child.type != "template_substitution":
                dynamic = True
                parts.append("\x00")
                continue
            inner = child.named_children[0] if child.named_children else None
            folded = _ts_fold_sql(inner) if inner is not None else None
            if folded is not None and folded[0] is not None and not folded[2]:
                parts.append(folded[0])
                continue
            dynamic = True
            parts.append(folded[1] if folded and folded[1] else "\x00")
        text = "".join(parts)
        return (None if dynamic else text), text, dynamic
    if node.type == "binary_expression" and _anon_op(node) == "+":
        left = _ts_fold_sql(node.child_by_field_name("left"))
        right = _ts_fold_sql(node.child_by_field_name("right"))
        if left is None or right is None:
            return None
        if left[0] is not None and right[0] is not None and not left[2] and not right[2]:
            text = left[0] + right[0]
            return text, text, False
        return None, left[1] + right[1], True
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func is None or func.type != "member_expression":
            return None
        prop = func.child_by_field_name("property")
        if prop is None or _text(prop) != "join":
            return None
        args = node.child_by_field_name("arguments")
        got = list(args.named_children) if args is not None else []
        if len(got) != 1 or _string(got[0]) != "":
            return None
        array = _unwrap(func.child_by_field_name("object"))
        if array is None or array.type != "array":
            return None
        bits: list[str] = []
        for elt in array.named_children:
            folded = _ts_fold_sql(elt)
            if folded is None or folded[0] is None or folded[2]:
                frag = "".join(bits) + (folded[1] if folded else "\x00")
                return None, frag, True
            bits.append(folded[0])
        text = "".join(bits)
        return text, text, False
    return None


def _outer_sql_expr(node) -> bool:
    if _ts_fold_sql(node) is None:
        return False
    parent = node.parent
    while parent is not None and parent.type in _FOLD_CONTAINERS:
        if _ts_fold_sql(parent) is not None:
            return False
        parent = parent.parent
    return True


def typescript_sql_findings(path: str, source: str) -> tuple[list[str], list[str]]:
    if path.endswith(".sql"):
        return _sql_file(path, source)
    try:
        parser = _parser()
    except (ImportError, OSError, ValueError, TypeError) as exc:
        return [], [f"dynamic DELETE {path} TypeScript parser unavailable: {exc}"]
    tree = parser.parse(source.encode("utf8"))
    if tree.root_node.has_error:
        return [], [f"dynamic DELETE {path} TypeScript parse error"]
    bad: list[str] = []
    gaps: list[str] = []
    for node in _walk(tree.root_node):
        if not _outer_sql_expr(node):
            continue
        line = node.start_point.row + 1
        where = f"{path}:{line}"
        full, fragments, dynamic = _ts_fold_sql(node) or (None, "", False)
        if node.type == "string" and _string(node) is None:
            found, holes = _note_sql(None, fragments, True, where)
            gaps.extend(holes)
            if any(item.startswith("DELETE ") for item in found):
                gaps.append(f"dynamic DELETE {where}")
            if any(item.startswith("verified SQL ") for item in found):
                gaps.append(f"dynamic authority SQL {where}")
            continue
        found, holes = _note_sql(full, fragments, dynamic, where)
        bad.extend(found)
        gaps.extend(holes)
    return list(dict.fromkeys(bad)), list(dict.fromkeys(gaps))
