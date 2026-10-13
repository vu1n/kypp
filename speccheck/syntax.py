"""Deterministic syntax facts. Cached by content hash, not by commit SHA."""

from __future__ import annotations

import ast
import copy
import re

from .cache import Cache
from .ts_syntax import parser_cache_key, typescript_mcp_tools


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
        if not isinstance(value, ast.List):
            return {"covered": False, "error": "_MIGRATIONS is not a list of string literals", "entries": []}
        found.append([elt.value if isinstance(elt, ast.Constant) and isinstance(elt.value, str) else None for elt in value.elts])
    if len(found) != 1:
        return {"covered": False, "error": f"expected one _MIGRATIONS list, found {len(found)}", "entries": []}
    return {"covered": all(entry is not None for entry in found[0]), "error": "nonliteral migration entry" if None in found[0] else None, "entries": found[0]}


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


def _bind_env(fn: ast.FunctionDef) -> dict[str, ast.AST]:
    env: dict[str, ast.AST] = {}
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            env[stmt.targets[0].id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            env[stmt.target.id] = stmt.value
    return env


def _substitute(node: ast.AST, env: dict[str, ast.AST]) -> ast.AST:
    class Subst(ast.NodeTransformer):
        def __init__(self) -> None:
            self.seen: set[str] = set()

        def visit_Name(self, name: ast.Name) -> ast.AST:
            if name.id in env and name.id not in self.seen:
                self.seen.add(name.id)
                return self.visit(copy.deepcopy(env[name.id]))
            return name

    return Subst().visit(copy.deepcopy(node))


def _is_rank_lookup(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = node.func.value
        return node.func.attr == "get" and isinstance(base, ast.Name) and base.id == "AUTHORITY_RANK"
    return isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "AUTHORITY_RANK"


def _negative_const(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value < 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        value = node.operand.value
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return False


def _positive_const(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value > 0
    )


def _reverses_rank(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Name):
            continue
        if sub.func.id == "reversed" and any(
            _mentions(arg, "AUTHORITY_RANK") or _mentions(arg, "AUTHORITIES") for arg in sub.args
        ):
            return True
        if sub.func.id == "sorted" and any(
            kw.arg == "reverse" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in sub.keywords
        ):
            if _mentions(sub, "AUTHORITY_RANK") or _mentions(sub, "AUTHORITIES"):
                return True
    return False


def _polarity(node: ast.AST | None) -> str:
    """How a key expression orders AUTHORITY_RANK under max(): ascending, flipped, unknown, or absent."""
    if node is None:
        return "absent"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _polarity(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _polarity(node.operand)
        if inner == "ascending":
            return "flipped"
        if inner == "flipped":
            return "ascending"
        return "unknown" if inner == "unknown" else "absent"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub) and _polarity(node.right) == "ascending":
        return "flipped"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _negative_const(node.left) or _negative_const(node.right):
            other = node.right if _negative_const(node.left) else node.left
            inner = _polarity(other)
            if inner == "ascending":
                return "flipped"
            if inner == "flipped":
                return "ascending"
            return "unknown" if inner == "unknown" else "absent"
        if _positive_const(node.left) or _positive_const(node.right):
            other = node.right if _positive_const(node.left) else node.left
            return _polarity(other)
    if _reverses_rank(node):
        return "flipped"
    if _is_rank_lookup(node):
        return "ascending"
    if _mentions(node, "AUTHORITY_RANK") or _mentions(node, "AUTHORITIES"):
        return "unknown"
    return "absent"


def _rank_order(fn: ast.FunctionDef) -> str:
    returns = [n for n in fn.body if isinstance(n, ast.Return)]
    if len(returns) != 1 or returns[0].value is None:
        return "unknown"
    expr = returns[0].value
    if isinstance(expr, ast.Tuple):
        if not expr.elts:
            return "unknown"
        expr = expr.elts[0]
    return _polarity(_substitute(expr, _bind_env(fn)))


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
    rank_order = _rank_order(rank_fns[0]) if len(rank_fns) == 1 else "unknown"
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
        "rank_order": rank_order,
        "survivor_uses_rank": survivor_ok,
    }
def classify_migration(sql: str) -> str:
    """Compatibility facade for bounded SQL policy analysis."""
    from .sqlsyntax import analyze_migration

    return analyze_migration(sql).kind


def cached_python_mcp(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "py-mcp", lambda: python_mcp_tools(source))


def cached_python_migrations(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "py-migrations-partial-v2", lambda: python_migrations(source))


def cached_rank(cache: Cache, vocab_source: str, arbiter_source: str) -> dict:
    blob = vocab_source + "\n---\n" + arbiter_source
    return cache.syntax(blob, "py-rank", lambda: python_authority_rank(vocab_source, arbiter_source))


def cached_ts_mcp(cache: Cache, source: str) -> dict:
    try:
        kind = parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return typescript_mcp_tools(source)
    return cache.syntax(source, kind, lambda: typescript_mcp_tools(source))
