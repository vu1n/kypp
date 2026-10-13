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


_CLOSED_EXTRA = {"forbid", "ignore"}


def _closed_extra_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in _CLOSED_EXTRA


def _target_name(target: ast.AST) -> str:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _model_config_open(value: ast.AST) -> bool:
    """True unless this config expression proves extra is absent or a literal forbid/ignore."""
    if isinstance(value, ast.Dict):
        for key, item in zip(value.keys, value.values):
            if key is None or not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return True
            if key.value == "extra" and not _closed_extra_literal(item):
                return True
        return False
    if isinstance(value, ast.Call):
        func = _dotted_name(value.func)
        leaf = func.split(".")[-1] if func else ""
        if leaf not in {"ConfigDict", "dict"} or value.args:
            return True
        for kw in value.keywords:
            if kw.arg is None or (kw.arg == "extra" and not _closed_extra_literal(kw.value)):
                return True
        return False
    return True


def _class_level_assigns(cls: ast.ClassDef):
    for stmt in cls.body:
        if isinstance(stmt, ast.ClassDef):
            yield from _class_level_assigns(stmt)
        elif isinstance(stmt, ast.Assign):
            yield stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            yield [stmt.target], stmt.value


def _class_extra_open(cls: ast.ClassDef) -> str | None:
    # Only a proved literal forbid/ignore is closed. Extra.allow, a name, or any other
    # non-literal policy is open: callers can still pass a field the model does not declare.
    for targets, value in _class_level_assigns(cls):
        for target in targets:
            name = _target_name(target)
            if name == "extra" and not _closed_extra_literal(value):
                return f"{cls.name} extra policy is not a literal forbid or ignore"
            if name == "model_config" and _model_config_open(value):
                return f"{cls.name} extra policy is not a literal forbid or ignore"
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
    """Bounded migration evidence. ``ok`` is not assurance, and ``unknown`` is unsupported syntax.

    Comments, strings, and quoted names are not clauses. A known non-additive statement
    stays a counterexample when a sibling statement is opaque.
    """
    from .sqlsyntax import analyze_migration

    return analyze_migration(sql).kind


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
    # partial-v2 keeps literal entries beside a nonliteral slot. The older all-or-nothing row does not.
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


_SQL_HOLE = "\x00"
_PROTECTED_TABLES = {"memory_claims", "observations"}
_SQL_STOP = {"from", "where", "group", "order", "limit", "union", "except", "intersect", "returning"}
_SQL_LITERAL = {"null", "current_timestamp", "current_date", "current_time", "true", "false"}


class _Tok:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: str) -> None:
        self.kind = kind
        self.value = value


def _scan_bracket(text: str, i: int) -> tuple[int, str]:
    i += 1
    buf: list[str] = []
    n = len(text)
    while i < n:
        if text[i] == _SQL_HOLE:
            return i + 1, _SQL_HOLE
        if text[i] == "]":
            if i + 1 < n and text[i + 1] == "]":
                buf.append("]")
                i += 2
                continue
            return i + 1, "".join(buf)
        buf.append(text[i])
        i += 1
    return i, "".join(buf)


def _scan_quoted(text: str, i: int, close: str, escape: str) -> tuple[int, str]:
    i += 1
    buf: list[str] = []
    n = len(text)
    while i < n:
        if text[i] == _SQL_HOLE:
            return i + 1, _SQL_HOLE
        if text[i] == close:
            if escape and i + 1 < n and text[i + 1] == escape:
                buf.append(close)
                i += 2
                continue
            return i + 1, "".join(buf)
        buf.append(text[i])
        i += 1
    return i, "".join(buf)


def _tokenize_sql(text: str) -> list[_Tok]:
    out: list[_Tok] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == _SQL_HOLE:
            out.append(_Tok("hole", ""))
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            continue
        if ch == "'":
            i, value = _scan_quoted(text, i, "'", "'")
            out.append(_Tok("hole", "") if value == _SQL_HOLE else _Tok("string", value.lower()))
            continue
        if ch in {'"', "`"}:
            i, value = _scan_quoted(text, i, ch, ch)
            out.append(_Tok("hole", "") if value == _SQL_HOLE else _Tok("quoted", value.lower()))
            continue
        if ch == "[":
            i, value = _scan_bracket(text, i)
            out.append(_Tok("hole", "") if value == _SQL_HOLE else _Tok("quoted", value.lower()))
            continue
        if ch == "?":
            out.append(_Tok("placeholder", "?"))
            i += 1
            continue
        if ch in "$@:" and i + 1 < n and (text[i + 1].isalnum() or text[i + 1] == "_"):
            j = i + 1
            if ch == "$" and text[j].isdigit():
                while j < n and text[j].isdigit():
                    j += 1
            elif text[j].isalpha() or text[j] == "_":
                j += 1
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
            else:
                out.append(_Tok("punct", ch))
                i += 1
                continue
            out.append(_Tok("placeholder", text[i:j]))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(_Tok("ident", text[i:j].lower()))
            i = j
            continue
        if ch.isdigit():
            j = i + 1
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            out.append(_Tok("number", text[i:j]))
            i = j
            continue
        out.append(_Tok("punct", ch))
        i += 1
    return out


def _is_ident(tok: _Tok, word: str | None = None) -> bool:
    if tok.kind != "ident":
        return False
    return word is None or tok.value == word


def _read_name(toks: list[_Tok], i: int) -> tuple[str | None, int]:
    """Return the identifier, ``None`` for a hole, and the index after it. Missing stays put."""
    if i >= len(toks):
        return "", i
    tok = toks[i]
    if tok.kind == "hole":
        return None, i + 1
    if tok.kind not in {"ident", "quoted"}:
        return "", i
    if (
        i + 2 < len(toks)
        and toks[i + 1].kind == "punct"
        and toks[i + 1].value == "."
        and toks[i + 2].kind in {"ident", "quoted", "hole"}
    ):
        if toks[i + 2].kind == "hole":
            return None, i + 3
        return toks[i + 2].value, i + 3
    return tok.value, i + 1


def _mentions_authority(toks: list[_Tok], start: int, end: int) -> bool:
    return any(tok.kind in {"ident", "quoted"} and tok.value == "authority" for tok in toks[start:end])


def _sets_verified(expr: list[_Tok]) -> bool:
    if len(expr) == 1 and expr[0].kind in {"string", "quoted"} and expr[0].value == "verified":
        return True
    for index, tok in enumerate(expr):
        if tok.kind != "ident" or tok.value != "else":
            continue
        nxt = index + 1
        if nxt < len(expr) and expr[nxt].kind == "punct" and expr[nxt].value == "(":
            nxt += 1
        if nxt < len(expr) and expr[nxt].kind in {"string", "quoted"} and expr[nxt].value == "verified":
            return True
    return False


def _expr_kind(expr: list[_Tok]) -> str:
    """verified, other (proved not the literal), or unknown."""
    if not expr:
        return "unknown"
    if _sets_verified(expr):
        return "verified"
    if any(tok.kind == "hole" for tok in expr):
        return "unknown"
    if len(expr) == 1:
        tok = expr[0]
        if tok.kind == "placeholder":
            return "unknown"
        if tok.kind in {"string", "quoted", "number"}:
            return "other"
        if tok.kind == "ident" and tok.value in _SQL_LITERAL:
            return "other"
    return "unknown"


def _split_exprs(toks: list[_Tok], i: int, *, paren: bool) -> tuple[list[list[_Tok]] | None, int]:
    """Split a comma-separated expression list. ``None`` when a surrounding paren does not close."""
    depth = 0
    if paren:
        if i >= len(toks) or toks[i].value != "(":
            return None, i
        i += 1
        depth = 1
    cur: list[_Tok] = []
    items: list[list[_Tok]] = []
    while i < len(toks):
        tok = toks[i]
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
            if not (paren and depth == 1 and not cur):
                cur.append(tok)
            i += 1
            continue
        if tok.kind == "punct" and tok.value == ")":
            depth -= 1
            if paren and depth == 0:
                items.append(cur)
                return items, i + 1
            cur.append(tok)
            i += 1
            continue
        if depth == (1 if paren else 0) and tok.kind == "punct" and tok.value == ",":
            items.append(cur)
            cur = []
            i += 1
            continue
        if not paren and depth == 0 and (
            (tok.kind == "punct" and tok.value == ";")
            or (tok.kind == "ident" and tok.value in _SQL_STOP)
        ):
            items.append(cur)
            return items, i
        cur.append(tok)
        i += 1
    if paren:
        return None, i
    items.append(cur)
    return items, i


def _authority_rows(cols: list[str] | None, rows: list[list[list[_Tok]]], mentioned: bool) -> tuple[bool, bool]:
    if cols is None:
        return False, mentioned
    if "authority" not in cols:
        return False, False
    bad = False
    for row in rows:
        if len(row) != len(cols):
            return bad, True
        for col, expr in zip(cols, row):
            if col != "authority":
                continue
            kind = _expr_kind(expr)
            if kind == "verified":
                bad = True
            elif kind == "unknown":
                return bad, True
    return bad, False


def _column_list(toks: list[_Tok], i: int) -> tuple[list[str] | None, int, bool]:
    """(columns, index, incomplete). A hole or a non-name means the list cannot be mapped."""
    names, i = _split_exprs(toks, i, paren=True)
    if names is None:
        return None, i, True
    cols: list[str] = []
    for expr in names:
        if len(expr) == 1 and expr[0].kind == "hole":
            return None, i, False
        if len(expr) == 1 and expr[0].kind in {"ident", "quoted"}:
            cols.append(expr[0].value)
            continue
        return None, i, False
    return cols, i, False


def _insert_authority(toks: list[_Tok], i: int) -> tuple[bool, bool, int]:
    j = i + 1
    if j < len(toks) and _is_ident(toks[j], "or"):
        j = min(len(toks), j + 2)
    if j >= len(toks) or not _is_ident(toks[j], "into"):
        return False, False, i + 1
    j += 1
    table, j = _read_name(toks, j)
    cols: list[str] | None = None
    if j < len(toks) and toks[j].kind == "punct" and toks[j].value == "(":
        cols, j, incomplete = _column_list(toks, j)
        if incomplete:
            return False, False, max(j, i + 1)
        if cols is None:
            bad, gap = _keep_claim_write(False, _mentions_authority(toks, i, j), table)
            return bad, gap, j
    if j < len(toks) and _is_ident(toks[j], "values"):
        j += 1
        rows: list[list[list[_Tok]]] = []
        while j < len(toks) and toks[j].kind == "punct" and toks[j].value == "(":
            row, j = _split_exprs(toks, j, paren=True)
            if row is None:
                return False, False, max(j, i + 1)
            rows.append(row)
            if j < len(toks) and toks[j].kind == "punct" and toks[j].value == ",":
                j += 1
                continue
            break
        if not rows:
            return False, False, max(j, i + 1)
        bad, gap = _authority_rows(cols, rows, _mentions_authority(toks, i, j))
        bad, gap = _keep_claim_write(bad, gap, table)
        return bad, gap, j
    if j < len(toks) and _is_ident(toks[j], "select"):
        items, j = _split_exprs(toks, j + 1, paren=False)
        if items is None:
            return False, False, max(j, i + 1)
        bad, gap = _authority_rows(cols, [items], _mentions_authority(toks, i, j))
        bad, gap = _keep_claim_write(bad, gap, table)
        return bad, gap, j
    if j < len(toks) and _is_ident(toks[j], "default"):
        end = min(len(toks), j + 2)
        mentioned = (cols is not None and "authority" in cols) or _mentions_authority(toks, i, end)
        bad, gap = _keep_claim_write(False, mentioned, table)
        return bad, gap, end
    return False, False, max(j, i + 1)


_SQL_VERBS = {"select", "insert", "update", "delete", "replace", "alter", "create", "with", "drop", "explain"}


def _split_statements(toks: list[_Tok]) -> list[list[_Tok]]:
    stmts: list[list[_Tok]] = []
    cur: list[_Tok] = []
    depth = 0
    for tok in toks:
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
        elif tok.kind == "punct" and tok.value == ")":
            depth = max(0, depth - 1)
        if depth == 0 and tok.kind == "punct" and tok.value == ";":
            if cur:
                stmts.append(cur)
            cur = []
            continue
        cur.append(tok)
    if cur:
        stmts.append(cur)
    return stmts


def _has_sql_verb(stmt: list[_Tok]) -> bool:
    """True for a SQL clause, not for prose that happens to contain ``with`` or ``authority``."""
    if not stmt:
        return False
    if stmt[0].kind == "ident" and stmt[0].value in _SQL_VERBS:
        return True
    for index, tok in enumerate(stmt):
        if tok.kind != "ident":
            continue
        nxt = stmt[index + 1] if index + 1 < len(stmt) else None
        if tok.value == "delete" and nxt is not None and _is_ident(nxt, "from"):
            return True
        if tok.value in {"insert", "replace"} and nxt is not None and _is_ident(nxt, "into"):
            return True
        if tok.value == "update" and nxt is not None and nxt.kind in {"ident", "quoted", "hole"}:
            if any(_is_ident(item, "set") for item in stmt[index + 1 : index + 8]):
                return True
        if tok.value == "alter" and nxt is not None and _is_ident(nxt, "table"):
            return True
        if tok.value == "create" and nxt is not None and _is_ident(nxt) and nxt.value in {
            "table", "index", "unique", "virtual", "trigger", "temp", "temporary",
        }:
            return True
        if tok.value == "drop" and nxt is not None and _is_ident(nxt) and nxt.value in {"table", "index", "trigger", "view"}:
            return True
        if tok.value == "truncate":
            return True
        if tok.value == "select" and any(_is_ident(item, "from") for item in stmt[index + 1 :]):
            return True
    return False


def _mentions_protected(stmt: list[_Tok]) -> bool:
    return any(tok.kind in {"ident", "quoted"} and tok.value in _PROTECTED_TABLES for tok in stmt)


def _has_add_column(stmt: list[_Tok]) -> bool:
    depth = 0
    for index, tok in enumerate(stmt):
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
        elif tok.kind == "punct" and tok.value == ")":
            depth = max(0, depth - 1)
        if depth == 0 and _is_ident(tok, "add") and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "column"):
            return True
    return False


def _alter_not_null_without_default(stmt: list[_Tok]) -> bool:
    has_default = False
    has_not_null = False
    for index, tok in enumerate(stmt):
        if _is_ident(tok, "default"):
            has_default = True
        if _is_ident(tok, "not") and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "null"):
            has_not_null = True
    return has_not_null and not has_default


def _forbidden_shape(stmt: list[_Tok]) -> bool:
    """Upsert, RETURNING, CTE, trigger, and REPLACE are outside the allow-list."""
    depth = 0
    for index, tok in enumerate(stmt):
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
        elif tok.kind == "punct" and tok.value == ")":
            depth = max(0, depth - 1)
            continue
        if depth != 0 or tok.kind != "ident":
            continue
        if tok.value in {"returning", "trigger", "with"}:
            return True
        # REPLACE INTO can hide a write. replace() is a function call.
        if tok.value == "replace" and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "into"):
            return True
        if tok.value == "on" and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "conflict"):
            return True
        if tok.value in {"insert", "update"} and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "or"):
            return True
    return False


def _unbounded_hole(stmt: list[_Tok]) -> bool:
    """A trailing hole can hide another statement. A hole with source after it is one slot."""
    for index, tok in enumerate(stmt):
        if tok.kind != "hole":
            continue
        if not any(item.kind != "hole" for item in stmt[index + 1 :]):
            return True
    return False



def _scan_deletes(stmt: list[_Tok]) -> tuple[bool, bool, bool, bool]:
    """protected hit, hole gap, every DELETE FROM target proved non-protected, saw DELETE."""
    bad = False
    gap = False
    saw = False
    targets: list[str | None] = []
    index = 0
    while index < len(stmt):
        if _is_ident(stmt[index], "delete"):
            saw = True
            if index + 1 < len(stmt) and _is_ident(stmt[index + 1], "from"):
                name, nxt = _read_name(stmt, index + 2)
                targets.append(name)
                if name in _PROTECTED_TABLES:
                    bad = True
                elif name is None:
                    gap = True
                index = max(nxt, index + 1)
                continue
        index += 1
    proved_safe = bool(targets) and all(name not in (None, "") and name not in _PROTECTED_TABLES for name in targets)
    return bad, gap, proved_safe, saw


def _set_region(stmt: list[_Tok], i: int) -> tuple[bool, bool, int]:
    bad = False
    gap = False
    while i < len(stmt):
        tok = stmt[i]
        if tok.kind == "ident" and tok.value in {"where", "returning", "from", "order", "limit"}:
            break
        if tok.kind == "punct" and tok.value == ";":
            break
        target, nxt = _read_name(stmt, i)
        if target == "" or nxt >= len(stmt) or stmt[nxt].value != "=":
            if _mentions_authority(stmt, i, len(stmt)):
                gap = True
            return bad, gap, i
        expr: list[_Tok] = []
        j = nxt + 1
        depth = 0
        while j < len(stmt):
            item = stmt[j]
            if item.kind == "punct" and item.value == "(":
                depth += 1
            elif item.kind == "punct" and item.value == ")" and depth:
                depth -= 1
            if depth == 0 and (
                (item.kind == "punct" and item.value in {",", ";"})
                or (item.kind == "ident" and item.value in {"where", "returning", "from"})
            ):
                break
            expr.append(item)
            j += 1
        if target is None or target == "authority":
            if target is None:
                gap = True
            else:
                kind = _expr_kind(expr)
                if kind == "verified":
                    bad = True
                elif kind == "unknown":
                    gap = True
        if j < len(stmt) and stmt[j].kind == "punct" and stmt[j].value == ",":
            i = j + 1
            continue
        return bad, gap, j
    return bad, gap, i


_CLAIM_TABLE = "memory_claims"


def _keep_claim_write(bad: bool, gap: bool, table: str | None) -> tuple[bool, bool]:
    """A verified-authority write is a violation only against claim storage.

    A known other table, such as ``build_jobs``, is neither a violation nor a gap.
    An unreadable table name stays unresolved rather than becoming a violation.
    """
    if table == _CLAIM_TABLE:
        return bad, gap
    if table is None or table == "":
        return False, bad or gap
    return False, False


def _statement_write_table(stmt: list[_Tok], at: int) -> str | None:
    """Table of the INSERT, REPLACE, or UPDATE that owns a clause at ``at``.

    ``DO UPDATE SET`` is not its own target; the INSERT before it is.
    ``None`` is a hole. ``""`` means no table was found.
    """
    for index in range(at - 1, -1, -1):
        tok = stmt[index]
        if tok.kind != "ident":
            continue
        if tok.value == "update":
            if index + 1 < len(stmt) and _is_ident(stmt[index + 1], "set"):
                continue
            name, _nxt = _read_name(stmt, index + 1)
            return name
        if tok.value in {"insert", "replace"}:
            cursor = index + 1
            if cursor < len(stmt) and _is_ident(stmt[cursor], "or"):
                cursor = min(len(stmt), cursor + 2)
            if cursor < len(stmt) and _is_ident(stmt[cursor], "into"):
                name, _nxt = _read_name(stmt, cursor + 1)
                return name
    return ""


def _scan_sets(stmt: list[_Tok]) -> tuple[bool, bool]:
    bad = False
    gap = False
    index = 0
    while index < len(stmt):
        if _is_ident(stmt[index], "set"):
            found_bad, found_gap, nxt = _set_region(stmt, index + 1)
            found_bad, found_gap = _keep_claim_write(found_bad, found_gap, _statement_write_table(stmt, index))
            bad = bad or found_bad
            gap = gap or found_gap
            index = max(nxt, index + 1)
            continue
        index += 1
    return bad, gap


def _scan_inserts(stmt: list[_Tok]) -> tuple[bool, bool]:
    bad = False
    gap = False
    index = 0
    while index < len(stmt):
        if _is_ident(stmt[index], "insert") or _is_ident(stmt[index], "replace"):
            subject = stmt
            if _is_ident(stmt[index], "replace"):
                subject = list(stmt)
                subject[index] = _Tok("ident", "insert")
            found_bad, found_gap, nxt = _insert_authority(subject, index)
            bad = bad or found_bad
            gap = gap or found_gap
            index = max(nxt, index + 1)
            continue
        index += 1
    return bad, gap


def _ddl_table(stmt: list[_Tok]) -> str | None:
    for index, tok in enumerate(stmt):
        if tok.kind == "ident" and tok.value in {"alter", "create"} and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "table"):
            name, _nxt = _read_name(stmt, index + 2)
            return name
    return ""


def _column_default_verified(stmt: list[_Tok]) -> tuple[bool, bool]:
    depth = 0
    found = False
    for index, tok in enumerate(stmt):
        if tok.kind == "punct" and tok.value == "(":
            depth += 1
        elif tok.kind == "punct" and tok.value == ")":
            depth = max(0, depth - 1)
        if tok.kind not in {"ident", "quoted"} or tok.value != "authority":
            continue
        local = depth
        j = index + 1
        while j < len(stmt):
            item = stmt[j]
            if item.kind == "punct" and item.value == "(":
                local += 1
            elif item.kind == "punct" and item.value == ")":
                if local == depth:
                    break
                local = max(depth, local - 1)
            elif local == depth and item.kind == "punct" and item.value == ",":
                break
            if item.kind == "ident" and item.value == "default":
                k = j + 1
                if k < len(stmt) and stmt[k].kind == "punct" and stmt[k].value in {"+", "-"}:
                    k += 1
                if k < len(stmt) and stmt[k].kind in {"string", "quoted"} and stmt[k].value == "verified":
                    found = True
                    break
            j += 1
    if not found:
        return False, False
    return _keep_claim_write(True, False, _ddl_table(stmt))


def _delete_like(stmt: list[_Tok]) -> bool:
    """DELETE, REPLACE INTO, INSERT OR REPLACE, DROP, or TRUNCATE.

    Naming a table is not enough. ``replace(...)`` is the SQL function, not a
    REPLACE statement.
    """
    for index, tok in enumerate(stmt):
        if tok.kind != "ident":
            continue
        if tok.value in {"delete", "drop", "truncate"}:
            return True
        if tok.value == "replace" and index + 1 < len(stmt) and _is_ident(stmt[index + 1], "into"):
            return True
        if (
            tok.value == "insert"
            and index + 2 < len(stmt)
            and _is_ident(stmt[index + 1], "or")
            and _is_ident(stmt[index + 2], "replace")
        ):
            return True
    return False


def _judge_statement(stmt: list[_Tok]) -> tuple[bool, bool, bool, bool, bool]:
    """Counterexamples and coverage gaps. Nothing here is a conformance proof."""
    delete_bad, delete_gap, proved_safe, saw_delete = _scan_deletes(stmt)
    verified_bad, verified_gap = _scan_sets(stmt)
    insert_bad, insert_gap = _scan_inserts(stmt)
    column_bad, column_gap = _column_default_verified(stmt)
    verified_bad = verified_bad or insert_bad or column_bad
    verified_gap = verified_gap or insert_gap or column_gap
    # Only a DELETE-like construction can be a delete candidate. A CREATE, ALTER,
    # INSERT, or SELECT that names a protected table is not one.
    if _delete_like(stmt) and not delete_bad and not proved_safe:
        if _mentions_protected(stmt) or any(tok.kind == "hole" for tok in stmt):
            delete_gap = True
    # Upsert, RETURNING, REPLACE, CTE, and an unbounded hole can hide an authority write.
    if (_forbidden_shape(stmt) or _unbounded_hole(stmt)) and not verified_bad:
        if _mentions_authority(stmt, 0, len(stmt)) or _mentions_protected(stmt):
            verified_gap = True
    unproved_delete = saw_delete and not delete_bad and not proved_safe
    return delete_bad, delete_gap, verified_bad, verified_gap, unproved_delete


def sql_write_flags(text: str, dynamic: bool) -> tuple[bool, bool, bool, bool]:
    """delete hit, delete gap, verified hit, verified gap for one folded SQL string.

    A proved ``DELETE`` from a protected table is a counterexample. A proved assignment
    of the literal ``verified`` to ``authority`` is a counterexample only when that
    write targets ``memory_claims``. ``UPDATE build_jobs SET authority='verified'``
    is not one. A ``?`` placeholder and any other untraced binding on claim storage
    are gaps for the authority writer. A statement that only names a protected table
    is not a delete gap. DELETE, REPLACE, INSERT OR REPLACE, DROP, and TRUNCATE are
    delete gaps when the target is protected or unproved. A counterexample is kept
    when a sibling statement is opaque.
    """
    delete_bad = False
    delete_gap = False
    verified_bad = False
    verified_gap = False
    unproved_delete = False
    for stmt in _split_statements(_tokenize_sql(text)):
        if not _has_sql_verb(stmt):
            continue
        bad, gap, vbad, vgap, unproved = _judge_statement(stmt)
        delete_bad = delete_bad or bad
        delete_gap = delete_gap or gap
        verified_bad = verified_bad or vbad
        verified_gap = verified_gap or vgap
        unproved_delete = unproved_delete or unproved
    if dynamic and unproved_delete and not delete_bad:
        delete_gap = True
    if delete_bad:
        delete_gap = False
    if verified_bad:
        verified_gap = False
    return delete_bad, delete_gap, verified_bad, verified_gap


def _const_text(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _fold_sql(node: ast.AST | None, env: dict[str, str | None]) -> tuple[str | None, str, bool]:
    """(full string or None, fragments with \\x00 holes, has a dynamic hole)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, node.value, False
    if isinstance(node, ast.Name):
        if node.id in env and env[node.id] is not None:
            value = env[node.id] or ""
            return value, value, False
        return None, _SQL_HOLE, True
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        dynamic = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            if isinstance(value, ast.FormattedValue):
                folded, frag, hole = _fold_sql(value.value, env)
                if hole or folded is None:
                    dynamic = True
                    parts.append(frag or _SQL_HOLE)
                else:
                    parts.append(folded)
                continue
            dynamic = True
            parts.append(_SQL_HOLE)
        text = "".join(parts)
        return (None if dynamic else text), text, dynamic
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, lfrag, lhole = _fold_sql(node.left, env)
        right, rfrag, rhole = _fold_sql(node.right, env)
        if left is not None and right is not None and not lhole and not rhole:
            return left + right, left + right, False
        return None, lfrag + rfrag, True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
        if isinstance(node.func.value, ast.Constant) and node.func.value.value == "" and len(node.args) == 1:
            arg = node.args[0]
            if isinstance(arg, (ast.List, ast.Tuple)):
                bits: list[str] = []
                for elt in arg.elts:
                    folded, frag, hole = _fold_sql(elt, env)
                    if hole or folded is None:
                        return None, "".join(bits) + (frag or _SQL_HOLE), True
                    bits.append(folded)
                text = "".join(bits)
                return text, text, False
    return None, "", False


def _coverage_hole(text: str) -> bool:
    """A trailing hole on a non-delete statement can hide SQL. That is coverage, not a delete candidate."""
    for stmt in _split_statements(_tokenize_sql(text)):
        if not _has_sql_verb(stmt) or _delete_like(stmt):
            continue
        if _unbounded_hole(stmt) and _mentions_protected(stmt):
            return True
    return False


def _walk_sql_strings(tree: ast.AST, path: str) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    gaps: list[str] = []
    env: dict[str, str | None] = {}

    def note(node: ast.AST, lineno: int) -> None:
        folded, fragments, dynamic = _fold_sql(node, env)
        text = folded if folded is not None else fragments
        if not text or not any(ch.isalnum() for ch in text):
            return
        where = f"{path}:{lineno}"
        delete_bad, delete_gap, verified_bad, verified_gap = sql_write_flags(text, dynamic)
        if delete_bad:
            bad.append(f"DELETE {where}")
        elif delete_gap:
            gaps.append(f"delete-like {where}")
        elif _coverage_hole(text):
            gaps.append(f"coverage {where}")
        if verified_bad:
            bad.append(f"verified SQL {where}")
        elif verified_gap:
            gaps.append(f"dynamic authority SQL {where}")

    def bind(target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            folded, _fragments, dynamic = _fold_sql(value, env)
            env[target.id] = None if dynamic or folded is None else folded

    def sql_expr(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            return True
        return False

    def note_tree(node: ast.AST, lineno: int) -> None:
        # Folded concatenation is one statement. Its leaves are not scanned on their own,
        # or a fragment such as "memory_claims" would be judged without the verb beside it.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if sql_expr(node):
            note(node, getattr(node, "lineno", lineno))
            return
        for sub in ast.iter_child_nodes(node):
            note_tree(sub, getattr(sub, "lineno", lineno))

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
                note_tree(stmt.value, getattr(stmt, "lineno", 0))
                continue
            if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                bind(stmt.target, stmt.value)
                note_tree(stmt.value, getattr(stmt, "lineno", 0))
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
                gaps.append(f"parse {path} did not parse: {exc.msg}")
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


# Context: doc://kypp/authority-order@0006#authority-dominates — only kypp.store.MemoryStore.mark_verified grants verified.
_VERIFIED_WRITER = ("kypp/store.py", "MemoryStore", "mark_verified")


def verified_authority_writes(files: dict[str, str]) -> dict:
    """Literal and traced writes of authority=verified. Dynamic writes outside mark_verified are gaps."""
    bad: list[str] = []
    gaps: list[str] = []
    scanned: list[str] = []
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
                    inside = (path, cls, fn) == _VERIFIED_WRITER
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
            # Allowed only for kypp.store.MemoryStore.mark_verified, not a same-named
            # method in another module. The SQL walker does not record the function.
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
    """Lines of kypp.store.MemoryStore.mark_verified. A lookalike class is not this method."""
    path, cls_name, method = _VERIFIED_WRITER
    source = files.get(path)
    if source is None:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[tuple[str, int]] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != cls_name:
            continue
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == method:
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
        # v3 drops cached rows that treated replace() as a REPLACE statement.
        kind = "delete-sql-v3-" + parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return delete_and_verified_sql(files)
    return cache.syntax(blob, kind, lambda: delete_and_verified_sql(files))


def cached_grader(cache: Cache, source: str) -> dict:
    return cache.syntax(source, "grader-claims", lambda: grader_claim_texts(source))


def cached_verified_writes(cache: Cache, files: dict[str, str]) -> dict:
    blob = _file_blob(files, ("kypp/", "worker/"))
    try:
        # v2 drops cached rows that treated replace() as a dynamic authority gap.
        kind = "verified-writes-v2-" + parser_cache_key()
    except (ImportError, OSError, ValueError, TypeError):
        return verified_authority_writes(files)
    return cache.syntax(blob, kind, lambda: verified_authority_writes(files))
