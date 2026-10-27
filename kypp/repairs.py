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

_SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;)\s*")  # a pipe stays inside its segment
_SETUP = {"cd", "source", ".", "export", "set", "pushd", "unset"}
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
# A later command led by one of these is looking or installing, not running the thing another way.
_NOT_A_REPAIR = {"which", "type", "command", "whereis", "hash", "echo", "printf", "ls", "cat", "man",
                 "file", "stat", "find", "fd", "rg", "grep", "sudo", "apt", "apt-get", "brew", "pip",
                 "pip3", "pipx", "conda", "cargo", "go", "gem", "npm", "pnpm", "yarn"}
_INSTALL_WORDS = {"install", "add", "i", "sync", "-v", "--version", "--help", "-h"}
# A value under one of these names may be a credential: name the variable, never repeat its value.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|AUTH|CRED|PRIVATE|COOKIE|SESSION", re.I)
_EXIT_CODE = re.compile(r"(?:Exit code:?|\"exit_code\":)\s*(-?\d+)")
_ABS = re.compile(r"(?:^|\s)/(?:var|tmp|private|Users|home|root)/")


@dataclass
class _Run:
    i: int
    command: str
    segments: list[list[str]]  # each shell segment, tokenized, env assignments kept as leading tokens
    output: str
    failed: bool


def _command_of(a: Action) -> str | None:
    if (a.name or "").lower() not in _SHELL_TOOLS or not isinstance(a.input, dict):
        return None
    c = a.input.get("command", a.input.get("cmd"))
    if isinstance(c, list):  # codex: ["bash", "-lc", "<script>"] or argv
        c = c[-1] if len(c) >= 3 and c[1] in ("-c", "-lc") else " ".join(map(str, c))
    return c.strip() if isinstance(c, str) and c.strip() else None


def _segments(command: str) -> list[list[str]]:
    """Each command in a `&&`/`;` chain, tokenized; only the first stage of a pipeline is kept."""
    out = []
    for seg in _SEGMENT_SPLIT.split(command):
        try:
            toks = shlex.split(seg)
        except ValueError:  # unbalanced quotes: a heredoc or a script, not one command
            continue
        toks = toks[:toks.index("|")] if "|" in toks else toks
        if toks:
            out.append(toks)
    return out


def _failed(a: Action) -> bool:
    """Claude records an error status; Codex records none, so read the exit code off the output."""
    if a.status == "error":
        return True
    m = _EXIT_CODE.search(a.output or "")
    return bool(m and m.group(1) != "0")


def _runs(trace: Trace) -> list[_Run]:
    runs = []
    for i, a in enumerate(trace.actions):
        cmd = _command_of(a)
        if cmd and "<<" not in cmd:  # a heredoc body isn't commands
            runs.append(_Run(i, cmd, _segments(cmd.replace("\n", "; ")), a.output or "", _failed(a)))
    return runs


def _split_env(toks: list[str]) -> tuple[dict[str, str], list[str]]:
    env, k = {}, 0
    if toks and toks[0] in ("env", "export"):
        k = 1
        while k < len(toks) and toks[k].startswith("-"):  # env -i, env -u NAME
            k += 2 if toks[k] in ("-u", "--unset", "-C", "--chdir", "-S") else 1
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


def _working_form(head: str, later: _Run) -> list[str] | None:
    """The tokens a later, successful command runs `head` with, when it runs it another way: behind a
    wrapper (`uv run pytest`, `python -m pytest`, `npx tsc`) or by a path (`.venv/bin/pytest`)."""
    for seg in later.segments:
        _, toks = _split_env(seg)
        for j, t in enumerate(toks):
            if _base(t) != head or (j == 0 and t == head):
                continue
            prefix = toks[:j]
            if prefix and (_base(prefix[0]) in _NOT_A_REPAIR or _INSTALL_WORDS & set(prefix)):
                break
            form = toks[:j + 1]
            return None if _ABS.search(" " + " ".join(form)) else form
    return None


def _installed_between(runs: list[_Run], a: _Run, b: _Run, name: str) -> bool:
    """An install of `name` between the two runs makes the second work for a reason the fact won't
    hold in the next session."""
    for r in runs:
        if a.i < r.i < b.i and name in r.command and _INSTALL_WORDS & set(r.command.split()):
            return True
    return False


def _bare_works_later(runs: list[_Run], a: _Run, head: str) -> bool:
    for r in runs:
        if r.i > a.i and not r.failed and not _errs(r.output) and any(_split_env(s)[1][:1] == [head] for s in r.segments):
            return True
    return False


def _errs(output: str) -> bool:
    return any(_first(ps, output) for ps in (_CMD_MISSING, _MOD_MISSING, _ENV_MISSING))


def _target(seg_toks: list[list[str]], missing_cmd: str | None) -> list[str] | None:
    """The segment the error is about: the one running the missing command, else the last one that
    isn't setup (`cd`, `source`, `export`)."""
    runs = [_split_env(s) for s in seg_toks]
    if missing_cmd:
        return next((s for s, (_, t) in zip(seg_toks, runs) if t and _base(t[0]) == missing_cmd), None)
    return next((s for s, (_, t) in reversed(list(zip(seg_toks, runs))) if t and t[0] not in _SETUP), None)


def _env_fix(name: str, head: str, later: _Run) -> str | None:
    """The later run that sets `name` and runs `head`, as the fact shows it."""
    for env, toks in map(_split_env, later.segments):
        if name in env and toks[:1] == [head]:
            value = env[name]
            # Never repeat a credential's value, or a host path, into shared memory.
            if _SECRET_NAME.search(name) or _ABS.search(" " + value) or len(value) > 80:
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
        # A piped run (`… | tail`) exits 0 with the error in its output, so the error text decides.
        missing_cmd = _first(_CMD_MISSING, a.output)
        missing_mod = None if missing_cmd else _first(_MOD_MISSING, a.output)
        missing_env = None if missing_cmd or missing_mod else _first(_ENV_MISSING, a.output)
        if not (missing_cmd or missing_mod or missing_env):
            continue
        seg = _target(a.segments, missing_cmd)
        env, toks = _split_env(seg or [])
        if not toks or "/" in toks[0] or not _WORD.match(head := toks[0]):
            continue
        if missing_cmd:
            if _bare_works_later(runs, a, head):
                continue  # it works bare later in the session: installed or PATH fixed, not a fact
            subject, why = f"`{head}` is not on PATH", "fails here (command not found)"
        elif missing_mod and _WORD.match(missing_mod):
            subject, why = f"`{head}` can't import `{missing_mod}`", f"fails here (no module `{missing_mod}`)"
        elif missing_env and missing_env not in env:
            subject, why = f"`{head}` needs `{missing_env}` set", f"fails here asking for `{missing_env}`"
        else:
            continue
        if subject not in drafts:
            for b in (r for r in runs if r.i > a.i and not r.failed and not _errs(r.output)):
                if missing_env:
                    fix = _env_fix(missing_env, head, b)
                elif (form := _working_form(head, b)) and not _installed_between(runs, a, b, missing_mod or head):
                    fix = f"`{shlex.join(form)} …`"
                else:
                    fix = None
                if fix:
                    drafts[subject] = ClaimDraft("pitfall", subject, f"{_render(toks, 2)} {why}; {fix} works.", 0.6)
                    break
        if subject in drafts:
            explained.add(a.i)
    return list(drafts.values()), explained
