"""repairs.py — §0 trace → "A fails here, B works" pitfalls, mined without a model.

An agent often rediscovers the same environment fact by failing at it: `pytest` is not on PATH but
`uv run pytest` works, `python` can't import the project but `.venv/bin/python` can, a script needs
`PYTHONPATH=src`. Within one session this mines a failing shell command followed by a later one that
does the same thing another way and works, and states both forms. The subject names only what failed
(never the fix), so the same fact from a second session reuses the subject and the arbiter's K=2
gate accepts it: one session's quirk stays a candidate, a recurring one becomes memory.

Idea borrowed from TokenEconomizer's environment facts (microsoft/TokenEconomizer, Apache-2.0):
its FormRepair and EnvValue families. Re-implemented here over §0 actions.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .distill import Action, ClaimDraft, Trace

# Shell tools as each harness names them (Claude: Bash; Codex: shell / exec_command; pillbox: bash).
_SHELL_TOOLS = {"bash", "shell", "exec_command", "local_shell", "run_shell_command"}

_OPS = {";", "&&", "||", "|", "&"}
_SETUP = {"cd", "source", ".", "export", "set", "pushd", "unset", ":"}
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_WORD = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")  # a command or module name we're willing to quote
# A missing command, as bash, sh, zsh and env each word it.
_CMD_MISSING = [re.compile(p) for p in (
    r"(?:^|\s|:)([A-Za-z0-9._+-]+): (?:command )?not found",
    r"command not found: ([A-Za-z0-9._+-]+)",
    r"env: '?([A-Za-z0-9._+-]+)'?: No such file or directory",
)]
_MOD_MISSING = [re.compile(p) for p in (
    r"No module named '?([A-Za-z0-9_.]+)'?",
    r"Cannot find module '([^']+)'",
)]
_ENV_MISSING = [re.compile(p) for p in (
    r"KeyError: '([A-Z][A-Z0-9_]+)'",
    r"\b([A-Z][A-Z0-9_]{2,})\b (?:environment variable )?(?:is )?(?:not set|unset|required|missing)",
    r"(?:environment variable|env var)s? '?([A-Z][A-Z0-9_]{2,})'? (?:is )?(?:not set|unset|required|missing)",
    r"[Mm]issing (?:required )?(?:environment variable|env var) '?([A-Z][A-Z0-9_]{2,})'?",
)]
# The wrappers a fix may run the command behind (or none, with the command given by path). Any
# other prefix (`which`, `touch`, `test -x`, `docker run -e K=V`) doesn't run it, or may carry a
# credential.
_WRAPPERS = {("uv", "run"), ("uvx",), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"),
             ("hatch", "run"), ("rye", "run"), ("python", "-m"), ("python3", "-m"), ("npx",),
             ("bunx",), ("bun", "x"), ("bun", "run"), ("pnpm", "exec"), ("pnpm", "dlx"),
             ("npm", "exec"), ("yarn",), ("yarn", "exec"), ("bundle", "exec")}
_INSTALLS = {"install", "add", "i", "sync"}  # a run with one of these may have installed anything
# Arguments that only ask the command about itself: running it this way proves nothing works.
_PROBE_ARGS = {"--version", "-V", "version", "--help", "-h", "help"}
_PROBE_FLAGS = {"--version", "-V", "--help", "-h"}  # with any other arguments it still only probes
# Only these variables' values are written; any other value may be a credential (a DATABASE_URL
# carries a password), so a fact names the variable and never repeats its value.
_SAFE_ENV = {"PYTHONPATH", "NODE_ENV", "CI", "TZ", "LANG", "LC_ALL", "RUST_BACKTRACE", "RUST_LOG",
             "DEBUG", "PORT", "HOST", "FORCE_COLOR", "NO_COLOR", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"}
_USERINFO = re.compile(r"://[^/\s]*@")
_EXIT_CODE = re.compile(r"(?:Exit code:?|\"exit_code\":|Process exited with code)\s*(-?\d+)")
# Any absolute or home-relative path is this host's, wherever it is rooted.
_ABS = re.compile(r"(?:^|[\s:=])[/~]")


@dataclass
class _Run:
    i: int
    command: str
    segments: list[list[str]]  # each shell segment, tokenized, env assignments kept as leading tokens
    proven: list[list[str]]  # the segments the run's success proves ran and succeeded
    output: str
    ok: bool  # confirmed to have succeeded; a run with no recorded outcome is neither
    may_fail: bool  # failed, or its exit status could hide a failure (`pytest | tail`, `pytest; echo`)
    where: tuple | None  # workdir and `cd` targets; None when a `cd` follows a command, so the place is unclear


def _command_of(a: Action) -> str | None:
    if (a.name or "").lower() not in _SHELL_TOOLS or not isinstance(a.input, dict):
        return None
    c = a.input.get("command", a.input.get("cmd"))
    if isinstance(c, list):  # codex: ["bash", "-lc", "<script>"] or argv
        c = c[-1] if len(c) >= 3 and c[1] in ("-c", "-lc") else shlex.join(map(str, c))
    return c.strip() if isinstance(c, str) and c.strip() else None


_MASK = {c: chr(0xE000 + i) for i, c in enumerate(";&|<>")}
_UNMASK = str.maketrans({v: k for k, v in _MASK.items()})


def _mask(command: str) -> str:
    """The command with quoted or escaped `;`, `&`, `|`, `<` and `>` swapped for placeholders, so the lexer,
    which drops quotes before it splits, never reads a printed `';'` as an operator. Comments are
    dropped and an unquoted newline separates commands, as the shell reads them."""
    out, quote, esc, comment = [], "", False, False
    for c in command:
        if comment:
            if c != "\n":
                continue
            comment = False
        if esc:
            if c == "\n":  # a line continuation joins the lines
                out.pop()
            else:
                out.append(_MASK.get(c, c))
            esc = False
            continue
        if c == "\\" and quote != "'":
            esc = True
        elif c in "'\"" and quote in ("", c):
            quote = "" if quote else c
        elif quote and c in _MASK:
            c = _MASK[c]
        elif not quote and c == "#" and (not out or out[-1] in " \t\n;&|()"):
            comment = True
            continue
        elif not quote and c in "<>" and out and out[-1].isdigit():
            fd = len(out)
            while fd and out[fd - 1].isdigit():
                fd -= 1
            if fd == 0 or out[fd - 1] in " \t;&|":  # `2>` names a descriptor; `2 >` keeps 2 as an argument
                del out[fd:]
        elif not quote and c == "\n":  # ends a command, unless the line ended on an operator
            c = " " if "".join(out).rstrip()[-1:] in ("", ";", "&", "|", "(") else ";"
        out.append(c)
    return "".join(out)


def _strip_redirects(toks: list[str]) -> list[str]:
    """The command's words without its redirections (`>out`, `2>&1`, `<in`), which aren't arguments."""
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
        elif t and set(t) <= set("<>&") and set(t) & set("<>"):
            skip = True
        else:
            out.append(t)
    return out


def _chain(command: str) -> list[tuple[list[str], str, str]]:
    """The command's segments as (tokens, operator before, operator after), split on shell operators
    outside quotes (`echo 'a; b'` is one segment, `|tail` two tokens). Unbalanced quotes (a heredoc
    or a script, not one command) yield nothing."""
    lex = shlex.shlex(_mask(command), posix=True, punctuation_chars=True)
    lex.whitespace_split, lex.commenters = True, ""  # _mask already dropped comments
    try:
        toks = list(lex)
    except ValueError:
        return []
    out, cur, before = [], [], ""
    for t in toks + [None]:  # None ends it: `''` lexes to an empty token
        t = "|" if t == "|&" else t  # bash's `|&` is a pipe that also carries stderr
        if t in _OPS or t is None:
            if cur:  # `> out.log` alone runs nothing but still sets the status: keep it as `:`
                words = _strip_redirects(cur) or [":"]
                out.append(([w.translate(_UNMASK) for w in words], before, t or ""))
            cur, before = [], t
        else:
            cur.append(t)
    if out and out[-1][2] == ";":  # a trailing `;` ends the command; its status is still the last one's
        out[-1] = (out[-1][0], out[-1][1], "")
    return out


def _segments(command: str) -> list[list[str]]:
    """Each command in the chain, tokenized; only the first stage of a pipeline is kept."""
    return [toks for toks, before, _ in _chain(command) if before != "|"]


def _proven(command: str) -> list[list[str]]:
    """The segments a successful run proves succeeded. A chain's exit status is its last command's,
    so only a segment followed by nothing but `&&` counts; `||` may skip a segment, a pipeline exits
    with its last stage, and `&` with the launch, so none of those prove anything."""
    if re.search(r"\$\(|`|[<>]\(", command):  # a substitution's status is hidden by the command around it
        return []
    chain = _chain(command)
    if any(before == "||" or after == "||" for _, before, after in chain):
        return []
    return [toks for k, (toks, before, after) in enumerate(chain)
            if before not in ("|", "&") and all(a in ("&&", "") for _, _, a in chain[k:])]


def _exit(a: Action) -> str | None:
    m = _EXIT_CODE.search(a.output or "")
    return m.group(1) if m else None


def _failed(a: Action) -> bool:
    return a.status == "error" or (_exit(a) or "0") != "0"


def _succeeded(a: Action) -> bool:
    """Confirmed success. Claude records a status; Codex records none, so its exit code decides. A
    run still `running`, or with no status and no exit code, can't prove a repair."""
    return not _failed(a) and (a.status == "completed" or _exit(a) == "0") and not _errs(a.output or "")


def _runs(trace: Trace) -> list[_Run]:
    runs, moved = [], 0
    for i, a in enumerate(trace.actions):
        cmd = _command_of(a)
        if cmd and "<<" not in cmd:  # a heredoc body isn't commands
            segs = _segments(cmd)
            moves = [t[0] in ("cd", "pushd", "popd") for t in segs]
            first_cmd = moves.index(False) if False in moves else len(moves)
            # A shell that persists (Claude's Bash) keeps a `cd` made on its own, so count those before this run;
            # `cd x && cmd` is read as self-contained, since its place is already in `where`.
            where = None if any(moves[first_cmd:]) else (
                moved, str(a.input.get("workdir") or a.input.get("cwd") or ""),
                *(" ".join(t[1:]) for t in segs[:first_cmd]))
            moved += all(moves)
            masked = any(op in ("|", ";", "||", "&") for _, _, op in _chain(cmd))
            runs.append(_Run(i, cmd, segs, _proven(cmd), a.output or "", _succeeded(a), _failed(a) or masked, where))
    return runs


def _split_env(toks: list[str]) -> tuple[dict[str, str], list[str]]:
    env, k = {}, 0
    if toks and toks[0] in ("env", "export"):
        k = 1
        while k < len(toks) and toks[k].startswith("-"):  # env -i, env -u NAME: a change, kept as one
            n = 2 if toks[k] in ("-u", "--unset", "-C", "--chdir", "-S") else 1
            env[" ".join(toks[k:k + n])] = ""
            k += n
    while k < len(toks) and (m := _ASSIGN.match(toks[k])):
        env[m.group(1)] = m.group(2)
        k += 1
    return env, toks[k:]


def _base(tok: str) -> str:
    return tok.rsplit("/", 1)[-1]


def _first(patterns, text: str) -> str | None:
    for p in patterns:
        if m := p.search(text):
            return m.group(1)
    return None


def _render(toks: list[str], upto: int) -> str:
    """A form as the fact shows it: its first words, then … for the rest."""
    return "`" + shlex.join(toks[:upto]) + (" …" if len(toks) > upto else "") + "`"


def _invocation(toks: list[str]) -> list[str] | None:
    """What a run of `toks[0]` runs: its script or `-m module` and their arguments, since a
    subcommand (`manage.py migrate` vs `check`) may import or read what another doesn't. A repair
    must run the same thing. Inline code (`-c`, `-e`) or flags ahead of the script aren't compared,
    so those runs yield no repair (None)."""
    rest = toks[1:]
    if rest[:1] == ["-m"] and len(rest) >= 2:
        return rest
    return rest if rest and not rest[0].startswith("-") else None


def _same(rest: list[str], args: list[str]) -> bool:
    """A run is the same invocation only with exactly its arguments: an added `--skip-db` may skip
    the failing import. A missing command has none to match, so any arguments do."""
    return rest == args if args else True


def _redact(form: list[str]) -> list[str]:
    """Never repeat a credential into shared memory: any `name=value` (`K=V`, `-e K=V`, `--token=x`)
    unless the name is known safe, and a URL's user:password."""
    out = []
    for t in form:
        key, eq, _ = t.partition("=")
        if eq and key not in _SAFE_ENV:
            t = f"{key}=…"
        out.append(_USERINFO.sub("://…@", t))
    return out


def _changes_env(segments: list[list[str]]) -> bool:
    return any(_split_env(seg)[0] or seg[0] in ("export", "set", "unset", "source", ".") for seg in segments)


def _working_form(head: str, args: list[str], later: _Run) -> list[str] | None:
    """The tokens a later, successful command runs `head` with, when it runs it another way: behind a
    wrapper (`uv run pytest`, `python -m pytest`, `npx tsc`) or by a path (`.venv/bin/pytest`)."""
    # An assignment or `export` anywhere in the run may be what made it work, and the fix wouldn't say so.
    if _changes_env(later.segments):
        return None
    for seg in later.proven:
        _, toks = _split_env(seg)
        for j, t in enumerate(toks):
            if _base(t) != head or (j == 0 and t == head):
                continue
            prefix, rest = toks[:j], toks[j + 1:]
            if prefix and (_base(prefix[0]), *prefix[1:]) not in _WRAPPERS:
                break
            if _probes(rest) or not _same(rest, args):
                break
            form = toks[:j + 1]
            # a host path, URL or `user@` may carry a home dir or a token; none belongs in shared memory
            return None if _ABS.search(" " + " ".join(form)) or any("://" in t or "@" in t for t in form) else form
    return None


_EDIT_TOOL = re.compile(r"edit|write|patch|replace", re.I)
# A shell command that writes files: sed/perl -i, tee, a `>` into a file, apply_patch, write_text.
_IN_PLACE = re.compile(r"\b(?:sed|perl)\s+-i|apply_patch|write_text")
_SHELL_EDIT = re.compile(r"\b(?:sed|perl)\s+-i|\btee\b|apply_patch|write_text|(?<![0-9&])>>?\s*(?!/dev/null|&)[\w./]")


def _edited_between(trace: Trace, a: _Run, b: _Run, head: str) -> bool:
    """A file edit between the runs, or before the invocation in the working run, may have changed
    the script itself (dropped the import), so the later run no longer tests what failed."""
    if any(_EDIT_TOOL.search(x.name or "") or _SHELL_EDIT.search(_command_of(x) or "")
           for x in trace.actions[a.i + 1:b.i]):
        return True
    def runs_head(seg: str) -> bool:  # as the command or behind a wrapper: `echo python > app.py` doesn't
        for toks, _, _ in _chain(seg.translate(_UNMASK)):
            w = _split_env(toks)[1]
            j = next((k for k, t in enumerate(w) if _base(t) == head), None)
            if j == 0 or (j and (_base(w[0]), *w[1:j]) in _WRAPPERS):
                return True
        return False
    # The invocation's own `> log` is output, not an edit; any other segment's write may be one.
    return any(_IN_PLACE.search(seg) if runs_head(seg) else _SHELL_EDIT.search(seg)
               for seg in re.split(r"[;&|]", _mask(b.command)))


def _same_place(a: _Run, b: _Run) -> bool:
    """A script name means the same script only from the same, known directory."""
    return a.where is not None and a.where == b.where


def _installed_between(runs: list[_Run], a: _Run, b: _Run) -> bool:
    """Any install or sync after the failure, up to and including the run that worked (`uv sync` and
    `pip install -r` don't name what they install), may be why it worked: the fact won't hold. So may
    an `export` or `source` between them, which persists in the shell."""
    return any(a.i < r.i <= b.i and any(_INSTALLS & set(t) for t, _, _ in _chain(r.command)) or a.i < r.i < b.i
               and _changes_env(r.segments) for r in runs)


def _probes(args: list[str]) -> bool:
    """The arguments only ask for a version or help, which can work while the real run can't."""
    return bool(args) and set(args) <= _PROBE_ARGS or bool(_PROBE_FLAGS & set(args))


def _works_unchanged_later(runs: list[_Run], a: _Run, head: str, args: list[str], same_place: bool) -> bool:
    """The same thing ran bare later, without the fix, and worked: installed or fixed mid-session."""
    for r in runs:
        if r.i > a.i and r.ok and (not same_place or _same_place(a, r)) and not _changes_env(r.segments):
            for env, toks in map(_split_env, r.proven):
                # unchanged: no env anywhere in the run, and a probe (`--help`) doesn't prove the real run works
                if toks[:1] == [head] and _same(toks[1:], args) and not env and not _probes(toks[1:]):
                    return True
    return False


def _errs(output: str) -> bool:
    return any(_first(ps, output) for ps in (_CMD_MISSING, _MOD_MISSING, _ENV_MISSING))


def _target(seg_toks: list[list[str]], missing_cmd: str | None) -> list[str] | None:
    """The segment the error is about: the one running the missing command, else the only one that
    isn't setup (`cd`, `source`, `export`). With two, a module or env error can't be attributed."""
    runs = [_split_env(s) for s in seg_toks]
    if missing_cmd:
        return next((s for s, (_, t) in zip(seg_toks, runs) if t and _base(t[0]) == missing_cmd), None)
    work = [s for s, (_, t) in zip(seg_toks, runs) if t and t[0] not in _SETUP]
    return work[0] if len(work) == 1 else None


def _env_fix(name: str, head: str, args: list[str], later: _Run) -> str | None:
    """The later run that sets `name` and runs the same thing, as the fact shows it."""
    for seg in later.proven:
        env, toks = _split_env(seg)
        others = list(later.segments)
        if seg in others:
            others.remove(seg)
        if _changes_env(others):  # other setup in the run may be part of the fix
            continue
        if set(env) == {name} and toks[:1] == [head] and _same(toks[1:], args) and not _probes(toks[1:]):
            value = env[name]
            # Only a plain value is written: a URL, `?token=`, `@` or host path may carry a secret.
            if name not in _SAFE_ENV or _ABS.search(" " + value) or "://" in value or not re.fullmatch(r"[\w.,:/+-]{0,80}", value):
                return f"`{name}=… {head} …`"
            return f"`{shlex.join([f'{name}={value}', head])} …`"
    return None


def mine(trace: Trace) -> tuple[list[ClaimDraft], set[int]]:
    """Repair pitfalls for one trace, and the indexes of the failed actions a repair explains."""
    from .distill import ClaimDraft  # distill imports this module; import back at call time

    runs = _runs(trace)
    drafts: dict[str, ClaimDraft] = {}
    explained: set[int] = set()
    for a in runs:
        # Error text counts only from a run that failed, or one whose status could hide it (`… | tail`):
        # a successful `rg "No module named"` just printed it.
        if not a.may_fail:
            continue
        missing_cmd = _first(_CMD_MISSING, a.output)
        missing_mod = None if missing_cmd else _first(_MOD_MISSING, a.output)
        missing_env = None if missing_cmd or missing_mod else _first(_ENV_MISSING, a.output)
        if not (missing_cmd or missing_mod or missing_env):
            continue
        seg = _target([t for t, _, _ in _chain(a.command)], missing_cmd)  # every stage: `a | b` may fail in b
        _, toks = _split_env(seg or [])
        if _changes_env(a.segments):  # `PATH=/x pytest` failing says nothing about this environment
            continue
        if not toks or "/" in toks[0] or not _WORD.match(head := toks[0]):
            continue
        args = [] if missing_cmd else _invocation(toks)
        if args is None:
            continue
        if _works_unchanged_later(runs, a, head, args, same_place=not missing_cmd):
            continue
        if missing_cmd:
            subject, why = f"`{head}` is not on PATH", "fails here (command not found)"
        elif missing_mod and _WORD.match(missing_mod):
            subject, why = f"`{head}` can't import `{missing_mod}`", f"fails here (no module `{missing_mod}`)"
        elif missing_env:
            subject, why = f"`{head}` needs `{missing_env}` set", f"fails here asking for `{missing_env}`"
        else:
            continue
        # Each failure needs its own repair to count as explained; the first one found writes the claim.
        # A script or module is only the same one run from the same place.
        for b in (r for r in runs if r.i > a.i and r.ok and (missing_cmd or _same_place(a, r))):
            if _installed_between(runs, a, b) or (not missing_cmd and _edited_between(trace, a, b, head)):
                fix = None
            elif missing_env:
                fix = _env_fix(missing_env, head, args, b)
            else:
                form = _working_form(head, args, b)
                # a relative path (`.venv/bin/pytest`, `uv run .venv/bin/pytest`) only works from where it ran
                if form and "/" in form[-1] and not _same_place(a, b):
                    form = None
                fix = form and f"`{shlex.join(form)} …`"
            if fix:
                shown = 1 + (2 if args[:1] == ["-m"] else len(args[:1]))  # the command and script, never its arguments
                drafts.setdefault(subject, ClaimDraft("pitfall", subject, f"{_render(_redact(toks), shown)} {why}; {fix} works.", 0.6))
                explained.add(a.i)
                break
    return list(drafts.values()), explained
