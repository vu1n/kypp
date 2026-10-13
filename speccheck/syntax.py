"""Deterministic syntax facts. Cached by content hash, not by commit SHA."""

from __future__ import annotations

import ast
import copy
import hashlib
import re

from .cache import Cache
from .ts_syntax import parser_cache_key, typescript_authority_rank, typescript_mcp_tools, typescript_sql_findings


def _is_mcp_tool(dec: ast.expr) -> bool:
    func = dec.func if isinstance(dec, ast.Call) else dec
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "tool"
        and isinstance(func.value, ast.Name)
        and func.value.id == "mcp"
    )


_SIMPLE_NAMES = {
    "str", "int", "float", "bool", "bytes", "bytearray", "object", "Any",
    "list", "dict", "set", "tuple", "frozenset",
    "Sequence", "Mapping", "MutableMapping", "Iterable", "Iterator", "Collection",
    "Optional", "Union", "Literal", "Annotated", "Final", "ClassVar",
    "Path", "datetime", "date", "Decimal", "NoneType",
}
_CONTAINERS = {
    "Optional", "list", "List", "set", "Set", "tuple", "Tuple", "Sequence",
    "Iterable", "Collection", "frozenset", "FrozenSet", "dict", "Dict", "Mapping", "MutableMapping",
}
_MODEL_BASES = {"BaseModel", "TypedDict"}


class _Model:
    def __init__(self, fields: list[str] | None, open_reason: str | None) -> None:
        self.fields = fields
        self.open_reason = open_reason


class _TypeEnv:
    def __init__(self) -> None:
        self.models: dict[str, _Model] = {}
        self.scalars: set[str] = set()
        self.unknown: dict[str, str] = {}


def _dotted_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _import_file(current: str, module: str | None, level: int) -> str | None:
    parts = current.split("/")
    if level:
        if len(parts) <= level:
            return None
        base = parts[: len(parts) - level]
        if module:
            base.extend(module.split("."))
        return "/".join(base) + ".py"
    if not module:
        return None
    return module.replace(".", "/") + ".py"


def _class_extra_open(cls: ast.ClassDef) -> str | None:
    for node in ast.walk(cls):
        if isinstance(node, ast.keyword) and node.arg == "extra":
            if isinstance(node.value, ast.Constant) and node.value.value == "allow":
                return f"{cls.name} sets extra=allow"
        if isinstance(node, ast.Assign):
            for target in node.targets:
                attr = target.attr if isinstance(target, ast.Attribute) else target.id if isinstance(target, ast.Name) else ""
                if attr == "extra" and isinstance(node.value, ast.Constant) and node.value.value == "allow":
                    return f"{cls.name} sets extra=allow"
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "extra"
                    and isinstance(value, ast.Constant)
                    and value.value == "allow"
                ):
                    return f"{cls.name} sets extra=allow"
    return None


def _bind_alias(env: _TypeEnv, name: str, value: ast.AST) -> None:
    if isinstance(value, ast.Subscript):
        outer = _dotted_name(value.value)
        if outer and outer.split(".")[-1] == "Literal":
            env.scalars.add(name)
            return
    label = _dotted_name(value)
    if label and (label.split(".")[-1] in _SIMPLE_NAMES or label in env.scalars):
        env.scalars.add(name)
        return
    if label and label in env.models:
        env.models[name] = env.models[label]


def _load_env(path: str, modules: dict[str, str], seen: dict[str, _TypeEnv]) -> _TypeEnv:
    if path in seen:
        return seen[path]
    env = _TypeEnv()
    seen[path] = env
    source = modules.get(path)
    if source is None:
        return env
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return env
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        dest = _import_file(path, node.module, node.level)
        sub = _load_env(dest, modules, seen) if dest and dest in modules else None
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name == "*":
                env.unknown["*"] = f"star import in {path}"
                continue
            if sub is None:
                env.unknown[local] = f"{local} is imported from outside the tree"
                continue
            if alias.name in sub.models:
                env.models[local] = sub.models[alias.name]
            elif alias.name in sub.scalars:
                env.scalars.add(local)
            elif alias.name in sub.unknown:
                env.unknown[local] = sub.unknown[alias.name]
            else:
                env.unknown[local] = f"{alias.name} imported from {dest} is not a resolved alias or model"
    pending: list[tuple[ast.ClassDef, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                label = _dotted_name(base)
                if label:
                    bases.append(label.split(".")[-1])
            if any(base in _MODEL_BASES or base in env.models for base in bases):
                pending.append((node, bases))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            _bind_alias(env, node.targets[0].id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            _bind_alias(env, node.target.id, node.value)
    left = pending
    while left:
        rest: list[tuple[ast.ClassDef, list[str]]] = []
        progressed = False
        for cls, bases in left:
            if not all(base in _MODEL_BASES or base in env.models for base in bases):
                rest.append((cls, bases))
                continue
            fields: list[str] = []
            open_reason = _class_extra_open(cls)
            for base in bases:
                if base in _MODEL_BASES:
                    continue
                model = env.models[base]
                if model.open_reason or model.fields is None:
                    open_reason = open_reason or model.open_reason or f"{base} has no closed field list"
                elif model.fields:
                    fields.extend(model.fields)
            for stmt in cls.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.append(stmt.target.id)
            if cls.name in env.models:
                open_reason = open_reason or f"{cls.name} is defined more than once"
            env.models[cls.name] = _Model(None if open_reason else fields, open_reason)
            progressed = True
        if not progressed:
            for cls, bases in rest:
                missing = [base for base in bases if base not in _MODEL_BASES and base not in env.models]
                env.models[cls.name] = _Model(None, f"{cls.name} extends unresolved {', '.join(missing) or 'model'}")
            break
        left = rest
    return env


def _merge_ann(left: tuple[str, list[str], str | None], right: tuple[str, list[str], str | None]) -> tuple[str, list[str], str | None]:
    if left[0] == "gap":
        return left
    if right[0] == "gap":
        return right
    fields = list(left[1])
    for name in right[1]:
        if name not in fields:
            fields.append(name)
    if fields:
        return "fields", fields, None
    return "scalar", [], None


def _annotation(node: ast.AST | None, env: _TypeEnv) -> tuple[str, list[str], str | None]:
    """scalar, fields of a closed model, or gap when the argument list is not proved."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return "scalar", [], None
    if isinstance(node, ast.Name):
        if node.id in _SIMPLE_NAMES or node.id in env.scalars:
            return "scalar", [], None
        model = env.models.get(node.id)
        if model is not None:
            if model.open_reason or model.fields is None:
                return "gap", [], model.open_reason or f"{node.id} has no closed field list"
            return "fields", list(model.fields), None
        return "gap", [], env.unknown.get(node.id, f"annotation {node.id} did not resolve")
    if isinstance(node, ast.Attribute):
        return "gap", [], f"annotation {_dotted_name(node)} did not resolve"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _merge_ann(_annotation(node.left, env), _annotation(node.right, env))
    if isinstance(node, ast.Subscript):
        outer = _dotted_name(node.value)
        outer = outer.split(".")[-1] if outer else ""
        if outer == "Unpack":
            return _annotation(node.slice, env)
        if outer == "Literal":
            return "scalar", [], None
        if outer == "Annotated":
            sl = node.slice
            first = sl.elts[0] if isinstance(sl, ast.Tuple) and sl.elts else sl
            return _annotation(first, env)
        if outer in _CONTAINERS:
            sl = node.slice
            parts = list(sl.elts) if isinstance(sl, ast.Tuple) else [sl]
            acc: tuple[str, list[str], str | None] = ("scalar", [], None)
            for part in parts:
                acc = _merge_ann(acc, _annotation(part, env))
            return acc
        return "gap", [], f"annotation {outer}[...] did not resolve"
    return "gap", [], "annotation did not resolve"


def _authority_in_body(fn: ast.AST) -> list[dict]:
    authority = []
    body = list(getattr(fn, "body", []) or [])
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
    return authority


def _tool_from_arguments(name: str, arguments: ast.arguments, lineno: int, env: _TypeEnv, fn: ast.AST | None) -> dict:
    gaps: list[str] = []
    params: list[str] = []
    if arguments.vararg is not None:
        gaps.append(f"{name} accepts *{arguments.vararg.arg}")
    for arg in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
        if arg.arg in {"self", "cls"}:
            continue
        kind, fields, reason = _annotation(arg.annotation, env)
        if kind == "gap":
            gaps.append(f"{name} parameter {arg.arg}: {reason}")
        params.append(arg.arg)
        for field in fields:
            if field not in params:
                params.append(field)
    if arguments.kwarg is not None:
        kind, fields, reason = _annotation(arguments.kwarg.annotation, env)
        if arguments.kwarg.annotation is None or kind != "fields":
            detail = f"{name} accepts **{arguments.kwarg.arg}"
            if reason:
                detail += f": {reason}"
            elif arguments.kwarg.annotation is not None:
                detail += " and the annotation is not a closed pydantic/TypedDict model"
            gaps.append(detail)
        else:
            for field in fields:
                if field not in params:
                    params.append(field)
    return {
        "name": name,
        "params": params,
        "lineno": lineno,
        "gaps": gaps,
        "authority": _authority_in_body(fn) if fn is not None else [],
    }


def _add_tool_callable(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg in {"fn", "function", "tool"}:
            return kw.value
    if not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Call):
        if arg.args:
            return arg.args[0]
        for kw in arg.keywords:
            if kw.arg in {"fn", "function", "tool"}:
                return kw.value
        return None
    return arg


def python_mcp_tools(
    source: str,
    modules: dict[str, str] | None = None,
    module_path: str = "kypp/mcp_server.py",
) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"covered": False, "error": f"syntax error: {exc.msg}", "tools": [], "add_tool": False, "gaps": []}
    mods = dict(modules or {})
    mods[module_path] = source
    env = _load_env(module_path, mods, {})
    functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    tools = []
    seen: set[int] = set()
    gaps: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_is_mcp_tool(dec) for dec in node.decorator_list):
            tools.append(_tool_from_arguments(node.name, node.args, node.lineno, env, node))
            seen.add(node.lineno)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_tool"):
            continue
        target = _add_tool_callable(node)
        if isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(target, ast.Lambda):
            tools.append(_tool_from_arguments("<lambda>", target.args, getattr(target, "lineno", node.lineno), env, None))
            continue
        if isinstance(target, ast.Name):
            found = functions.get(target.id) or []
            if len(found) == 1 and found[0].lineno not in seen:
                tools.append(_tool_from_arguments(found[0].name, found[0].args, found[0].lineno, env, found[0]))
                seen.add(found[0].lineno)
                continue
            if len(found) == 1:
                continue
            if not found:
                gaps.append(f"mcp.add_tool({target.id}) does not resolve to a function in {module_path}")
            else:
                gaps.append(f"mcp.add_tool({target.id}) matches more than one function")
            continue
        gaps.append(f"mcp.add_tool at line {getattr(node, 'lineno', '?')} does not resolve to a local function")
    return {"covered": True, "error": None, "tools": tools, "add_tool": bool(gaps), "gaps": gaps}


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


def cached_python_mcp(
    cache: Cache,
    source: str,
    modules: dict[str, str] | None = None,
    module_path: str = "kypp/mcp_server.py",
) -> dict:
    mods = modules or {}
    extra = "\n".join(
        f"{path}\0{hashlib.sha256(text.encode()).hexdigest()}"
        for path, text in sorted(mods.items())
        if path != module_path
    )
    blob = module_path + "\n" + source + "\n" + extra
    return cache.syntax(blob, "py-mcp", lambda: python_mcp_tools(source, mods, module_path))


def cached_python_migrations(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "py-migrations", lambda: python_migrations(source))


def cached_rank(cache: Cache, vocab_source: str, arbiter_source: str) -> dict:
    blob = vocab_source + "\n---\n" + arbiter_source
    return cache.syntax(blob, "py-rank", lambda: python_authority_rank(vocab_source, arbiter_source))


def cached_ts_mcp(cache: Cache, source: str) -> dict:
    try:
        kind = parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return typescript_mcp_tools(source)
    return cache.syntax(source, kind, lambda: typescript_mcp_tools(source))


_DELETE_TARGET = re.compile(r"\bDELETE\s+FROM\s+[\"'`]?(memory_claims|observations)\b", re.I)
_DELETE_WORD = re.compile(r"\bDELETE\b", re.I)
_VERIFIED_SQL = re.compile(
    r"\b(?:UPDATE|INSERT)\b[\s\S]*?(?:"
    r"\bauthority\s*=\s*['\"]verified['\"]"
    r"|\bauthority\b[\s\S]{0,160}?ELSE\s+['\"]verified['\"]"
    r")",
    re.I,
)


def _const_text(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _fold_sql(node: ast.AST | None, env: dict[str, str | None]) -> tuple[str | None, str, bool]:
    """(full string or None, constant fragments, has a dynamic hole)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, node.value, False
    if isinstance(node, ast.Name):
        if node.id in env and env[node.id] is not None:
            value = env[node.id] or ""
            return value, value, False
        return None, "", True
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        dynamic = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                folded, _frag, hole = _fold_sql(value.value, env)
                if hole or folded is None:
                    dynamic = True
                else:
                    parts.append(folded)
                continue
            dynamic = True
        text = "".join(parts)
        return (None if dynamic else text), text, dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, lfrag, lhole = _fold_sql(node.left, env)
        right, rfrag, rhole = _fold_sql(node.right, env)
        if left is not None and right is not None:
            return left + right, left + right, False
        return None, lfrag + rfrag, True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        if isinstance(node.func.value, ast.Constant) and node.func.value.value == "" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, (ast.List, ast.Tuple)):
                bits: list[str] = []
                for elt in arg.elts:
                    folded, _frag, hole = _fold_sql(elt, env)
                    if hole or folded is None:
                        return None, "".join(bits), True
                    bits.append(folded)
                text = "".join(bits)
                return text, text, False
    return None, "", False


def _sql_findings(folded: str | None, fragments: str, dynamic: bool, where: str, kind: str) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    gaps: list[str] = []
    text = folded if folded is not None else fragments
    if kind == "delete":
        if text and _DELETE_TARGET.search(text):
            bad.append(where)
        elif dynamic and _DELETE_WORD.search(fragments):
            gaps.append(where)
    elif kind == "verified":
        if text and _VERIFIED_SQL.search(text):
            bad.append(where)
        elif dynamic and re.search(r"\bauthority\s*=\s*['\"]?\s*$", fragments) and re.search(r"\b(?:UPDATE|INSERT)\b", fragments, re.I):
            gaps.append(where)
    return bad, gaps


def _walk_sql_strings(tree: ast.AST, path: str) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    gaps: list[str] = []
    env: dict[str, str | None] = {}

    def note(node: ast.AST, lineno: int) -> None:
        folded, fragments, dynamic = _fold_sql(node, env)
        if not fragments and folded is None:
            return
        for kind in ("delete", "verified"):
            found, holes = _sql_findings(folded, fragments, dynamic, f"{path}:{lineno}", kind)
            if kind == "delete":
                bad.extend(f"DELETE {item}" for item in found)
                gaps.extend(f"dynamic DELETE {item}" for item in holes)
            else:
                bad.extend(f"verified SQL {item}" for item in found)
                gaps.extend(f"dynamic authority SQL {item}" for item in holes)

    def bind(target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            folded, _fragments, dynamic = _fold_sql(value, env)
            env[target.id] = None if dynamic or folded is None else folded

    def note_tree(node: ast.AST, lineno: int) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                note(sub, getattr(sub, "lineno", lineno))
            elif isinstance(sub, (ast.JoinedStr, ast.BinOp, ast.Call)):
                note(sub, getattr(sub, "lineno", lineno))

    def walk(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                saved = dict(env)
                walk(stmt.body)
                env.clear()
                env.update(saved)
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    bind(target, stmt.value)
                note(stmt.value, getattr(stmt, "lineno", 0))
                continue
            if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                bind(stmt.target, stmt.value)
                note(stmt.value, getattr(stmt, "lineno", 0))
                continue
            if isinstance(stmt, (ast.For, ast.While, ast.If, ast.With, ast.Try)):
                saved = dict(env)
                note_tree(stmt, getattr(stmt, "lineno", 0))
                walk(list(stmt.body))
                walk(list(getattr(stmt, "orelse", [])))
                walk(list(getattr(stmt, "finalbody", [])))
                env.clear()
                env.update(saved)
                continue
            note_tree(stmt, getattr(stmt, "lineno", 0))

    walk(tree.body if isinstance(tree, ast.Module) else [])
    return list(dict.fromkeys(bad)), list(dict.fromkeys(gaps))




def delete_and_verified_sql(files: dict[str, str]) -> dict:
    bad: list[str] = []
    gaps: list[str] = []
    scanned: list[str] = []
    for path in sorted(files):
        if path.startswith("kypp/") and path.endswith(".py"):
            scanned.append(path)
            try:
                tree = ast.parse(files[path])
            except SyntaxError as exc:
                gaps.append(f"{path} did not parse: {exc.msg}")
                continue
            found, holes = _walk_sql_strings(tree, path)
            bad.extend(found)
            gaps.extend(holes)
        elif path.startswith("worker/") and path.endswith((".ts", ".sql")):
            scanned.append(path)
            found, holes = typescript_sql_findings(path, files[path])
            bad.extend(found)
            gaps.extend(holes)
    return {"scanned": scanned, "bad": bad, "gaps": gaps}


_UNKNOWN = object()
_LEAK = "leak"
_CLEAN = "clean"
_GAP = "unknown"
_PURE_CALLS = {"len", "grader_scheme", "_trunc", "int", "str", "float", "min", "max", "round", "abs", "bool", "sorted", "set", "tuple", "list"}
_TEXT_METHODS = {"strip", "lower", "upper", "lstrip", "rstrip", "split", "replace"}


def _combine_class(parts: list[str]) -> str:
    if _LEAK in parts:
        return _LEAK
    if _GAP in parts:
        return _GAP
    return _CLEAN


def _expr_class(node: ast.AST | None, env: dict[str, str]) -> str:
    if node is None or isinstance(node, ast.Constant):
        return _CLEAN
    if isinstance(node, ast.Name):
        return env.get(node.id, _GAP)
    if isinstance(node, ast.JoinedStr):
        return _combine_class([_expr_class(value, env) for value in node.values])
    if isinstance(node, ast.FormattedValue):
        return _expr_class(node.value, env)
    if isinstance(node, (ast.BinOp, ast.BoolOp, ast.Compare, ast.UnaryOp)):
        kids = list(ast.iter_child_nodes(node))
        return _combine_class([_expr_class(kid, env) for kid in kids if isinstance(kid, ast.expr)])
    if isinstance(node, ast.IfExp):
        return _combine_class([_expr_class(node.test, env), _expr_class(node.body, env), _expr_class(node.orelse, env)])
    if isinstance(node, ast.Attribute):
        base = _expr_class(node.value, env)
        if node.attr == "feedback":
            return _LEAK
        if node.attr == "name":
            return _GAP if base != _LEAK else _LEAK
        return base if base == _LEAK else _CLEAN
    if isinstance(node, ast.Subscript):
        base = _expr_class(node.value, env)
        key = node.slice
        key_text = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
        if key_text in {"name", "feedback"}:
            return _LEAK
        if base == _LEAK:
            return _LEAK
        return _GAP
    if isinstance(node, ast.Call):
        return _call_class(node, env)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return _combine_class([_expr_class(elt, env) for elt in node.elts])
    if isinstance(node, ast.Dict):
        parts = [_expr_class(key, env) for key in node.keys if key is not None]
        parts.extend(_expr_class(value, env) for value in node.values)
        return _combine_class(parts)
    return _GAP


def _call_class(node: ast.Call, env: dict[str, str]) -> str:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "get":
        key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        if key in {"name", "feedback"}:
            return _LEAK
        base = _expr_class(func.value, env)
        return _LEAK if base == _LEAK else _GAP
    if isinstance(func, ast.Attribute) and func.attr in _TEXT_METHODS:
        return _combine_class([_expr_class(func.value, env), *[_expr_class(arg, env) for arg in node.args]])
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    if name in _PURE_CALLS:
        return _combine_class([_expr_class(arg, env) for arg in list(node.args) + [kw.value for kw in node.keywords]])
    return _GAP


def _claim_draft_fields(call: ast.Call) -> tuple[ast.AST | None, ast.AST | None]:
    subject = call.args[1] if len(call.args) > 1 else None
    content = call.args[2] if len(call.args) > 2 else None
    for kw in call.keywords:
        if kw.arg == "subject":
            subject = kw.value
        elif kw.arg == "content":
            content = kw.value
    return subject, content


def _bind_target(target: ast.AST, value: ast.AST, env: dict[str, str]) -> None:
    if isinstance(target, ast.Name):
        env[target.id] = _expr_class(value, env)
    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
        for elt, part in zip(target.elts, value.elts):
            _bind_target(elt, part, env)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                env[elt.id] = _GAP


def grader_claim_texts(source: str) -> dict:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"covered": False, "error": f"syntax error: {exc.msg}", "results": []}

    results: list[dict] = []

    def note_calls(node: ast.AST, env: dict[str, str]) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            fname = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if fname != "ClaimDraft":
                continue
            subject, content = _claim_draft_fields(sub)
            kind = _combine_class([_expr_class(subject, env), _expr_class(content, env)])
            results.append({"lineno": getattr(sub, "lineno", None), "class": kind})

    def scan(stmts: list[ast.stmt], env: dict[str, str]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = {arg.arg: _GAP for arg in stmt.args.posonlyargs + stmt.args.args + stmt.args.kwonlyargs}
                scan(stmt.body, inner)
                continue
            if isinstance(stmt, ast.ClassDef):
                scan(stmt.body, dict(env))
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    _bind_target(target, stmt.value, env)
                note_calls(stmt.value, env)
                continue
            if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                _bind_target(stmt.target, stmt.value, env)
                note_calls(stmt.value, env)
                continue
            if isinstance(stmt, ast.For):
                iter_env = dict(env)
                if isinstance(stmt.target, ast.Name):
                    iter_env[stmt.target.id] = _GAP
                elif isinstance(stmt.target, (ast.Tuple, ast.List)):
                    for elt in stmt.target.elts:
                        if isinstance(elt, ast.Name):
                            iter_env[elt.id] = _GAP
                scan(list(stmt.body), iter_env)
                scan(list(stmt.orelse), dict(env))
                continue
            if isinstance(stmt, ast.With):
                scan(list(stmt.body), dict(env))
                continue
            if isinstance(stmt, (ast.If, ast.While)):
                note_calls(stmt.test, env)
                scan(list(stmt.body), dict(env))
                scan(list(stmt.orelse), dict(env))
                continue
            if isinstance(stmt, ast.Try):
                scan(list(stmt.body), dict(env))
                scan(list(stmt.orelse), dict(env))
                scan(list(stmt.finalbody), dict(env))
                continue
            note_calls(stmt, env)

    scan(tree.body, {})
    return {"covered": True, "error": None, "results": results}


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _enclosing_names(stack: list[ast.AST]) -> tuple[str | None, str | None]:
    cls = fn = None
    for node in stack:
        if isinstance(node, ast.ClassDef):
            cls = node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
    return cls, fn


def verified_authority_writes(files: dict[str, str]) -> dict:
    """Literal and traced writes of authority=verified. Dynamic writes outside mark_verified are gaps."""
    bad: list[str] = []
    gaps: list[str] = []
    scanned: list[str] = []
    allowed = ("MemoryStore", "mark_verified")
    for path in sorted(files):
        if not (path.startswith("kypp/") and path.endswith(".py")):
            continue
        scanned.append(path)
        try:
            tree = ast.parse(files[path])
        except SyntaxError as exc:
            gaps.append(f"{path} did not parse: {exc.msg}")
            continue
        module_env: dict[str, str | None] = {}

        def fold_name(node: ast.AST, env: dict[str, str | None]) -> str | None | object:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                if node.id not in env:
                    return _UNKNOWN
                return _UNKNOWN if env[node.id] is None else env[node.id]
            return _UNKNOWN

        def walk(stmts: list[ast.stmt], env: dict[str, str | None], stack: list[ast.AST]) -> None:
            for stmt in stmts:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    walk(stmt.body, dict(env), stack + [stmt])
                    continue
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    folded = fold_name(stmt.value, env)
                    env[stmt.targets[0].id] = None if folded is _UNKNOWN else folded
                for sub in ast.walk(stmt):
                    if not isinstance(sub, ast.Call) or _call_name(sub.func) != "claim":
                        continue
                    cls, fn = _enclosing_names(stack)
                    inside = (cls, fn) == allowed
                    values: list[tuple[ast.AST, str]] = []
                    for kw in sub.keywords:
                        if kw.arg == "authority":
                            values.append((kw.value, "keyword"))
                        elif kw.arg is None:
                            gaps.append(f"{path}:{getattr(sub, 'lineno', '?')} claim(**...) may pass authority")
                    if len(sub.args) > 11:
                        values.append((sub.args[11], "positional"))
                    for value, how in values:
                        folded = fold_name(value, env)
                        where = f"{path}:{getattr(sub, 'lineno', '?')} claim authority {how}"
                        if folded == "verified" and not inside:
                            bad.append(where)
                        elif folded is _UNKNOWN and not inside:
                            gaps.append(where)
                if isinstance(stmt, (ast.For, ast.While, ast.If, ast.With, ast.Try)):
                    walk(list(stmt.body), dict(env), stack)
                    walk(list(getattr(stmt, "orelse", [])), dict(env), stack)
                    walk(list(getattr(stmt, "finalbody", [])), dict(env), stack)

        walk(tree.body, module_env, [])
    sql = delete_and_verified_sql(files)
    for item in sql["bad"]:
        if item.startswith("verified SQL "):
            loc = item[len("verified SQL ") :]
            path = loc.split(":", 1)[0]
            # Allowed only when the Python string sits in MemoryStore.mark_verified.
            # The SQL walker does not record the function, so re-check via AST line lookup below.
            bad.append(item)
        elif item.startswith("DELETE "):
            continue
    for item in sql["gaps"]:
        if item.startswith("dynamic authority SQL"):
            gaps.append(item)
    allowed_lines = _mark_verified_lines(files)
    bad = [item for item in bad if not _is_allowed_verified(item, allowed_lines)]
    return {"scanned": scanned, "bad": bad, "gaps": gaps, "sql_scanned": sql["scanned"]}


def _mark_verified_lines(files: dict[str, str]) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for path, source in files.items():
        if not (path.startswith("kypp/") and path.endswith(".py")):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != "MemoryStore":
                continue
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "mark_verified":
                    end = getattr(stmt, "end_lineno", stmt.lineno)
                    for line in range(stmt.lineno, end + 1):
                        found.add((path, line))
    return found


def _is_allowed_verified(item: str, allowed: set[tuple[str, int]]) -> bool:
    if not item.startswith("verified SQL "):
        return False
    loc = item[len("verified SQL ") :]
    path, _, line_s = loc.partition(":")
    try:
        line = int(line_s)
    except ValueError:
        return False
    return (path, line) in allowed


def _file_blob(files: dict[str, str], prefixes: tuple[str, ...]) -> str:
    parts = []
    for path in sorted(files):
        if path.startswith(prefixes):
            parts.append(path + "\0" + hashlib.sha256(files[path].encode()).hexdigest())
    return "\n".join(parts)


def cached_ts_rank(cache: Cache, source: str) -> dict:
    try:
        kind = "ts-rank-" + parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return typescript_authority_rank(source)
    return cache.syntax(source, kind, lambda: typescript_authority_rank(source))


def cached_delete_sql(cache: Cache, files: dict[str, str]) -> dict:
    blob = _file_blob(files, ("kypp/", "worker/"))
    try:
        kind = "delete-sql-" + parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return delete_and_verified_sql(files)
    return cache.syntax(blob, kind, lambda: delete_and_verified_sql(files))


def cached_grader(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "grader-claims", lambda: grader_claim_texts(source))


def cached_verified_writes(cache: Cache, files: dict[str, str]) -> dict:
    blob = _file_blob(files, ("kypp/", "worker/"))
    try:
        kind = "verified-writes-" + parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return verified_authority_writes(files)
    return cache.syntax(blob, kind, lambda: verified_authority_writes(files))
