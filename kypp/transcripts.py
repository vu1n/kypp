#!/usr/bin/env python3
"""transcripts.py — translate external coding-agent transcripts (Claude Code, Codex) into §0 events.

The dogfood corpus (~/.claude/projects, ~/.codex/sessions) is orders of magnitude larger than pillbox
§0 logs, but in a different shape. This module is the bridge: one native transcript → the same §0 event
stream that wire.capture_events and the distiller already consume — now including the user_message /
assistant_message turns the conversational-turns channel carries. It is the shared FRONT-END for
`kypp seed` (→ claims) and the eval-task miner (→ frozen tasks): it owns translation, per-repo session
discovery, AND the eval-contamination policy, so both back-ends stay thin and can't drift on which
sessions count. The distiller back-end is unchanged.

Source-decoupled like wire.py: the translators return (events, meta) and never touch the store. Meta
carries cwd (for project derivation + eval-contamination filtering) and the producing model(s).

Defensive throughout — these are large, external, occasionally-malformed JSONL files read read-only.
"""
from __future__ import annotations

import glob
import json
import os
import re

from .distill import parse_jsonl

_OUT = 2000  # per-action output cap carried into a §0 tool_call (the distiller re-clips anyway)


# --- Claude Code: ~/.claude/projects/<path-key>/<session>.jsonl --------------------------------

def _claude_text(content) -> str:
    """Flatten a Claude message content (str | list of blocks) to plain text — text blocks and the
    textual part of tool_result blocks; tool_use blocks contribute nothing here (handled as actions)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            out.append(b.get("text", ""))
        elif b.get("type") == "tool_result":
            c = b.get("content")
            out.append(c if isinstance(c, str) else _claude_text(c))
    return "\n".join(s for s in out if s)


def claude_events(path: str) -> tuple[list[dict], dict]:
    """One Claude Code session transcript → (§0 events, meta). Emits, in document order: message_end
    (per distinct model), user_message / assistant_message (NL turns), and tool_call — each correlated
    with the user tool_result that reports its outcome (which arrives in a later turn)."""
    with open(path, encoding="utf-8") as f:
        rows = parse_jsonl(f)
    sid = next((r.get("sessionId", "") for r in rows if r.get("sessionId")), "")
    cwd = next((r.get("cwd", "") for r in rows if r.get("cwd")), "")
    # First pass: tool outcomes (a tool_result lives in the user turn AFTER its tool_use).
    results: dict = {}
    for r in rows:
        if r.get("type") == "user":
            content = (r.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        results[b.get("tool_use_id")] = {
                            "is_error": bool(b.get("is_error")),
                            "output": _claude_text(b.get("content"))}
    # Second pass: emit in order.
    events: list[dict] = []
    models: list[str] = []
    first_user, n_calls, n_user, n_asst = "", 0, 0, 0
    for r in rows:
        msg = r.get("message") or {}
        kind = r.get("type")
        if kind == "assistant":
            m = msg.get("model")
            if m and m not in models:
                models.append(m)
                events.append({"sessionId": sid, "payload": {"type": "message_end", "model": m}})
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        n_asst += 1
                        events.append({"sessionId": sid, "payload": {
                            "type": "assistant_message", "text": b["text"].strip()}})
                    elif b.get("type") == "tool_use":
                        n_calls += 1
                        res = results.get(b.get("id"), {})
                        status = ("error" if res.get("is_error") else
                                  "completed" if b.get("id") in results else "unspecified")
                        events.append({"sessionId": sid, "payload": {
                            "type": "tool_call", "name": b.get("name", ""), "status": status,
                            "title": "", "input": b.get("input"),
                            "output": (res.get("output") or "")[:_OUT]}})
        elif kind == "user" and not r.get("isSidechain"):
            content = msg.get("content")
            # the human-authored text ONLY — not the tool_result blocks that share the user turn
            if isinstance(content, str):
                txt = content.strip()
            elif isinstance(content, list):
                txt = "\n".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text").strip()
            else:
                txt = ""
            if txt:
                n_user += 1
                first_user = first_user or txt
                events.append({"sessionId": sid, "payload": {"type": "user_message", "text": txt}})
    meta = {"session_id": sid, "cwd": cwd, "models": models, "task": first_user[:400],
            "n_calls": n_calls, "n_user_turns": n_user, "n_asst_turns": n_asst, "source": "claude"}
    return events, meta


# --- Codex: ~/.codex/sessions/**/rollout-*.jsonl (and archived_sessions) -----------------------

def codex_events(path: str) -> tuple[list[dict], dict]:
    """One Codex rollout → (§0 events, meta). Codex streams the same content under both `event_msg`
    and `response_item`; we key actions by call_id (so the duplicate stream collapses) and take NL
    turns from user_message / agent_message. Best-effort: Codex carries no explicit tool status."""
    with open(path, encoding="utf-8") as f:
        rows = parse_jsonl(f)
    sid = os.path.basename(path)
    cwd, models = "", []
    calls: dict = {}     # call_id -> {name, input}
    outputs: dict = {}   # call_id -> output
    turns: list = []     # ordered (role, text)
    for r in rows:
        p = r.get("payload") or {}
        pt = p.get("type")
        if r.get("type") == "session_meta" or pt == "session_meta":
            cwd = cwd or p.get("cwd") or r.get("cwd") or ""
            # the model id only — NOT model_provider ('openai'): a provider token would get scrubbed
            # out of shared claim content by the model-agnostic redaction (distill.redact_for_scope).
            mp = p.get("model")
            if mp and mp not in models:
                models.append(mp)
        elif pt == "function_call":
            try:
                args = json.loads(p.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {"arguments": p.get("arguments")}
            cid = p.get("call_id") or f"#{len(calls)}"  # distinct id-less calls must not collapse to one
            calls.setdefault(cid, {"name": p.get("name", ""), "input": args})
        elif pt == "function_call_output":
            outputs.setdefault(p.get("call_id"), str(p.get("output", "")))
        elif pt == "user_message":
            t = (p.get("message") or "").strip()
            if t:
                turns.append(("user", t))
        elif pt == "agent_message":
            t = (p.get("message") or "").strip()
            if t:
                turns.append(("assistant", t))
    events: list[dict] = []
    for m in models:
        events.append({"sessionId": sid, "payload": {"type": "message_end", "model": m}})
    for role, text in turns:
        events.append({"sessionId": sid, "payload": {
            "type": "user_message" if role == "user" else "assistant_message", "text": text}})
    for cid, c in calls.items():
        out = outputs.get(cid, "")
        events.append({"sessionId": sid, "payload": {
            "type": "tool_call", "name": c["name"], "status": "unspecified",  # codex carries no pass/fail
            "title": "", "input": c["input"], "output": out[:_OUT]}})
    first_user = next((t for role, t in turns if role == "user"), "")
    meta = {"session_id": sid, "cwd": cwd, "models": models, "task": first_user[:400],
            "n_calls": len(calls), "n_user_turns": sum(r == "user" for r, _ in turns),
            "n_asst_turns": sum(r == "assistant" for r, _ in turns), "source": "codex"}
    return events, meta


# --- dispatch ----------------------------------------------------------------------------------

def transcript_events(path: str) -> tuple[list[dict], dict]:
    """Translate any supported transcript, sniffing the shape from the path / first line. Claude Code
    transcripts live under .claude/projects; Codex rollouts are named rollout-*.jsonl under .codex."""
    base = os.path.basename(path)
    if "/.codex/" in path or base.startswith("rollout-"):
        return codex_events(path)
    return claude_events(path)


# --- repo discovery + corpus policy (shared by `kypp seed` and the eval-task miner) ------------

def _claude_root() -> str:
    return os.path.expanduser(os.environ.get("KYPP_CLAUDE_ROOT", "~/.claude/projects"))


def _codex_roots() -> tuple[str, ...]:
    env = os.environ.get("KYPP_CODEX_ROOTS")
    roots = env.split(":") if env else ("~/.codex/sessions", "~/.codex/archived_sessions")
    return tuple(os.path.expanduser(r) for r in roots)


def _claude_dir_key(repo: str) -> str:
    """Claude Code's project-dir encoding of an abspath: every non-alphanumeric char → '-' (so '/' AND
    '.' both map, and runs are NOT collapsed — '/.claude' → '--claude'). Deliberately NOT _pillbox's
    project_for_cwd, which preserves dots: a dotted path (e.g. a `.claude/worktrees` checkout, or any
    `foo.bar` dir) lands in a DIFFERENT on-disk dir than the kypp project bucket, so DISCOVERY must use
    Claude's own encoder or it silently globs a nonexistent dir and finds zero sessions."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.expanduser(repo)))


def claude_sessions(repo: str) -> list[str]:
    """Claude Code transcripts for a repo — ~/.claude/projects/<claude-dir-key>/*.jsonl. The dir name
    is Claude's encoding of the repo path, so these are already this-repo by construction."""
    return sorted(glob.glob(os.path.join(_claude_root(), _claude_dir_key(repo), "*.jsonl")))


def codex_sessions() -> list[str]:
    """All Codex rollouts — not organized by repo, so consumers filter by each session's recorded cwd."""
    return sorted(p for root in _codex_roots()
                  for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))


# A session whose WORK was a frozen benchmark task (Exercism / SWE-bench / toolz / sensitivity) must
# not seed memory we later evaluate on (leak), and must not be mined back out as a "new" task. cwd +
# first-prompt markers; conservative — better to skip a real session than to contaminate the benchmark.
_EVAL_CWD = ("swebench", "exercism", "/eval/tasks", "toolz-tasks", "sensitivity-tasks", "/eval/memory/tasks")
_EVAL_TASK = ("# instructions", "exercism", "swebench", "segment 1 of", "segment 2 of", "run grade.sh")


def is_eval_contaminated(meta: dict) -> bool:
    """True if a session looks like frozen-benchmark work (cwd under an eval task tree, or a first
    prompt with the benchmark's pedagogical markers) — excluded from BOTH seeding and task-mining so
    memory and the benchmark stay disjoint (the seed-or-donate-never-both discipline)."""
    cwd = (meta.get("cwd") or "").lower()
    if any(m in cwd for m in _EVAL_CWD):
        return True
    task = (meta.get("task") or "").lower()
    return any(m in task for m in _EVAL_TASK)


if __name__ == "__main__":
    from .distill import build_trace

    # Claude: a human turn, an assistant turn + a tool_use, the user tool_result (error), one more call.
    claude_rows = [
        {"type": "user", "sessionId": "s1", "cwd": "/Users/x/code/foo",
         "message": {"role": "user", "content": "fix the build"}},
        {"type": "assistant", "sessionId": "s1",
         "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
             {"type": "text", "text": "I'll run the build."},
             {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "cargo build"}}]}},
        {"type": "user", "sessionId": "s1",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1",
                                  "is_error": True, "content": "error: missing feature"}]}},
        {"type": "assistant", "sessionId": "s1",
         "message": {"model": "claude-opus-4-8", "content": [
             {"type": "tool_use", "id": "tu2", "name": "Edit",
              "input": {"file_path": "Cargo.toml"}}]}},
    ]
    import tempfile
    p = tempfile.mktemp(suffix=".jsonl")
    with open(p, "w") as f:
        for r in claude_rows:
            f.write(json.dumps(r) + "\n")
    events, meta = claude_events(p)
    t = build_trace(events, task=meta["task"])
    types = [e["payload"]["type"] for e in events]
    assert meta["cwd"] == "/Users/x/code/foo", meta
    assert meta["task"] == "fix the build", meta
    assert types.count("user_message") == 1 and types.count("assistant_message") == 1, types
    assert types.count("tool_call") == 2 and types.count("message_end") == 1, types
    assert len(t.turns) == 2, t.turns  # build_trace folds the turns in
    bash = next(e["payload"] for e in events if e["payload"].get("name") == "Bash")
    assert bash["status"] == "error" and "missing feature" in bash["output"], bash
    edit = next(e["payload"] for e in events if e["payload"].get("name") == "Edit")
    assert edit["status"] == "unspecified", edit  # no tool_result for tu2

    # Claude's dir encoder maps every non-alnum → '-' (dots too), unlike project_for_cwd — the
    # silent-zero-sessions bug on dotted paths. Lock it.
    assert _claude_dir_key("/U/code/a.b/.claude/wt") == "-U-code-a-b--claude-wt", _claude_dir_key("/U/code/a.b/.claude/wt")

    # Codex: session_meta (cwd), a user_message, a function_call + its output, an agent_message.
    codex_rows = [
        {"type": "session_meta", "payload": {"type": "session_meta", "cwd": "/Users/x/code/bar",
                                             "model_provider": "openai"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "add a flag"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "arguments": "{\"cmd\":\"pwd\"}", "call_id": "c1"}},
        {"type": "event_msg", "payload": {"type": "function_call", "name": "exec_command",
                                          "arguments": "{\"cmd\":\"pwd\"}", "call_id": "c1"}},  # dup
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                              "output": "/Users/x/code/bar"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "Done — added the flag."}},
    ]
    pc = tempfile.mktemp(suffix=".jsonl")
    with open(pc, "w") as f:
        for r in codex_rows:
            f.write(json.dumps(r) + "\n")
    cev, cmeta = codex_events(pc)
    ctypes = [e["payload"]["type"] for e in cev]
    assert cmeta["cwd"] == "/Users/x/code/bar", cmeta
    assert ctypes.count("tool_call") == 1, ctypes  # the duplicate call_id collapsed
    assert ctypes.count("user_message") == 1 and ctypes.count("assistant_message") == 1, ctypes
    assert cmeta["task"] == "add a flag", cmeta

    print(f"OK — claude: {len(events)} §0 events ({meta['n_user_turns']}u+{meta['n_asst_turns']}a turns, "
          f"{meta['n_calls']} calls, error-correlated); codex: {len(cev)} events (dup call_id collapsed)")
