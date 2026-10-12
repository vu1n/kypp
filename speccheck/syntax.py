"""Deterministic syntax facts. Cached by content hash, not by commit SHA."""

from __future__ import annotations

import ast
import re

from .cache import Cache


def _is_mcp_tool(dec: ast.expr) -> bool:
    func = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "tool"
        and isinstance(func.value, ast.Name)
        and func.value.id == "mcp"
    )


def _param_names(fn: ast.FunctionDef) -> list[str]:
    names = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
    return [name for name in names if name not in {"self", "cls"}]


def python_mcp_tools(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"covered": False, "error": f"syntax error: {exc.msg}", "tools": [], "add_tool": False}
    tools = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_mcp_tool(dec) for dec in node.decorator_list):
            continue
        gaps = []
        if node.args.kwarg is not None:
            gaps.append(f"{node.name} accepts **{node.args.kwarg.arg}")
        authority = []
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        for child in body:
            for sub in ast.walk(child):
                if not isinstance(sub, ast.Call):
                    continue
                for kw in sub.keywords:
                    if kw.arg != "authority":
                        continue
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        authority.append({"value": kw.value.value, "lineno": getattr(sub, "lineno", None), "dynamic": False})
                    else:
                        authority.append({"value": None, "lineno": getattr(sub, "lineno", None), "dynamic": True})
                func = sub.func
                name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if name == "claim" and len(sub.args) > 11:
                    positional = sub.args[11]
                    if isinstance(positional, ast.Constant) and isinstance(positional.value, str):
                        authority.append({"value": positional.value, "lineno": getattr(sub, "lineno", None), "dynamic": False})
                    else:
                        authority.append({"value": None, "lineno": getattr(sub, "lineno", None), "dynamic": True})
        tools.append(
            {
                "name": node.name,
                "params": _param_names(node),
                "lineno": node.lineno,
                "gaps": gaps,
                "authority": authority,
            }
        )
    add_tool = bool(re.search(r"\badd_tool\s*\(", source))
    return {"covered": True, "error": None, "tools": tools, "add_tool": add_tool}


def python_migrations(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"covered": False, "error": f"syntax error: {exc.msg}", "entries": []}
    found = []
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_MIGRATIONS":
                    target, value = t, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_MIGRATIONS":
            target, value = node.target, node.value
        if target is None:
            continue
        if not isinstance(value, ast.List) or not all(isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in value.elts):
            return {"covered": False, "error": "_MIGRATIONS is not a list of string literals", "entries": []}
        found.append([elt.value for elt in value.elts])  # type: ignore[union-attr]
    if len(found) != 1:
        return {"covered": False, "error": f"expected one _MIGRATIONS list, found {len(found)}", "entries": []}
    return {"covered": True, "error": None, "entries": found[0]}


def _assigned(tree: ast.AST, name: str) -> ast.AST | None:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def _literal_strings(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, ast.Subscript):
        return None
    value = node.value
    if not isinstance(value, ast.Name) or value.id != "Literal":
        return None
    sl = node.slice
    elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
    out = []
    for elt in elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        else:
            return None
    return out


def _mentions(node: ast.AST | None, name: str) -> bool:
    if node is None:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
    return False


def _dictcomp_enumerates_authorities(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.DictComp) or len(node.generators) != 1:
        return False
    iter_ = node.generators[0].iter
    return (
        isinstance(iter_, ast.Call)
        and isinstance(iter_.func, ast.Name)
        and iter_.func.id == "enumerate"
        and len(iter_.args) == 1
        and isinstance(iter_.args[0], ast.Name)
        and iter_.args[0].id == "AUTHORITIES"
    )


def _authorities_from_authority(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_args"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "Authority"
    )


def python_authority_rank(vocab_source: str, arbiter_source: str) -> dict:
    try:
        vocab = ast.parse(vocab_source)
        arbiter = ast.parse(arbiter_source)
    except SyntaxError as exc:
        return {"covered": False, "error": f"syntax error: {exc.msg}"}
    literal = _literal_strings(_assigned(vocab, "Authority"))
    rank_node = _assigned(vocab, "AUTHORITY_RANK")
    authorities_node = _assigned(vocab, "AUTHORITIES")
    rank_fns = [n for n in ast.walk(arbiter) if isinstance(n, ast.FunctionDef) and n.name == "_rank"]
    survivors = [n for n in ast.walk(arbiter) if isinstance(n, ast.FunctionDef) and n.name == "_survivor"]
    first_ok = False
    if len(rank_fns) == 1:
        returns = [n for n in rank_fns[0].body if isinstance(n, ast.Return)]
        if len(returns) == 1 and isinstance(returns[0].value, ast.Tuple) and returns[0].value.elts:
            first_ok = _mentions(returns[0].value.elts[0], "AUTHORITY_RANK")
    survivor_ok = False
    if len(survivors) == 1:
        for sub in ast.walk(survivors[0]):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "max":
                for kw in sub.keywords:
                    if kw.arg == "key" and isinstance(kw.value, ast.Name) and kw.value.id == "_rank":
                        survivor_ok = True
    covered = literal is not None and rank_node is not None and len(rank_fns) == 1 and len(survivors) == 1
    return {
        "covered": covered,
        "error": None if covered else "authority rank shape was not fully recognized",
        "literal": literal,
        "rank_from_authorities": _dictcomp_enumerates_authorities(rank_node) and _authorities_from_authority(authorities_node),
        "rank_fn_first": first_ok,
        "survivor_uses_rank": survivor_ok,
    }


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


def _property_keys(obj: str) -> list[str] | None:
    """Keys of a `{...}` object at depth 1, skipping strings."""
    if not obj.startswith("{"):
        return None
    end = _match_delim(obj, 0, "{", "}")
    if end is None:
        return None
    keys: list[str] = []
    i = 1
    depth = 1
    state = "code"
    while i < end:
        c = obj[i]
        nxt = obj[i + 1] if i + 1 < end else ""
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
            if c == {"sq": "'", "dq": '"', "bt": "`"}[state]:
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
        if c in {"'", '"', "`"}:
            state = {"'": "sq", '"': "dq", "`": "bt"}[c]
            i += 1
            continue
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        if depth == 1 and (c.isalpha() or c == "_" or c in {'"', "'"}):
            if c in {'"', "'"}:
                q = c
                j = i + 1
                while j < end and obj[j] != q:
                    j += 2 if obj[j] == "\\" else 1
                key = obj[i + 1 : j]
                k = j + 1
            else:
                j = i
                while j < end and (obj[j].isalnum() or obj[j] in {"_", "$"}):
                    j += 1
                key = obj[i:j]
                k = j
            while k < end and obj[k].isspace():
                k += 1
            if k < end and obj[k] == ":":
                keys.append(key)
                i = k + 1
                continue
        i += 1
    return keys


def typescript_mcp_tools(source: str) -> dict:
    match = re.search(r"\bconst\s+TOOLS\s*=\s*\[", source)
    if not match:
        return {"covered": False, "error": "no const TOOLS array", "tools": []}
    start = match.end() - 1
    end = _match_delim(source, start, "[", "]")
    if end is None:
        return {"covered": False, "error": "TOOLS array did not close", "tools": []}
    body = source[start : end + 1]
    tools = []
    i = 1
    while i < len(body) - 1:
        if body[i] == "{":
            close = _match_delim(body, i, "{", "}")
            if close is None:
                return {"covered": False, "error": "tool object did not close", "tools": []}
            obj = body[i : close + 1]
            name_m = re.search(r"\bname\s*:\s*[\"']([^\"']+)[\"']", obj)
            props: list[str] = []
            prop_m = re.search(r"\bproperties\s*:\s*\{", obj)
            if prop_m:
                brace = prop_m.end() - 1
                keys = _property_keys(obj[brace:])
                if keys is None:
                    return {"covered": False, "error": f"properties of {name_m.group(1) if name_m else '?'} did not parse", "tools": []}
                props = keys
            tools.append({"name": name_m.group(1) if name_m else None, "params": props})
            i = close + 1
            continue
        i += 1
    if not tools:
        return {"covered": False, "error": "TOOLS array had no objects", "tools": []}
    return {"covered": True, "error": None, "tools": tools}


_ADD_COLUMN = re.compile(r"\bADD\s+COLUMN\b", re.I)
_NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.I)
_DEFAULT = re.compile(r"\bDEFAULT\b", re.I)
_VERSION_TABLE = re.compile(r"\b(schema_version|schema_migrations|schema_versions|alembic_version)\b", re.I)


def classify_migration(sql: str) -> str:
    compact = " ".join(sql.split())
    if _VERSION_TABLE.search(compact):
        return "version_table"
    if not _ADD_COLUMN.search(compact):
        return "not_add_column"
    if _NOT_NULL.search(compact) and not _DEFAULT.search(compact):
        return "not_null_without_default"
    return "ok"


def cached_python_mcp(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "py-mcp", lambda: python_mcp_tools(source))


def cached_python_migrations(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "py-migrations", lambda: python_migrations(source))


def cached_rank(cache: Cache, vocab_source: str, arbiter_source: str) -> dict:
    blob = vocab_source + "\n---\n" + arbiter_source
    return cache.syntax(blob, "py-rank", lambda: python_authority_rank(vocab_source, arbiter_source))


def cached_ts_mcp(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "ts-mcp", lambda: typescript_mcp_tools(source))
