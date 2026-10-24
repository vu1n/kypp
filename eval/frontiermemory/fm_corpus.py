#!/usr/bin/env python3
"""FrontierMemory release corpus (joint-674, Hugging Face) → kypp §0 events.

The corpus is what a memory agent sees: opaque files under `inputs/`, each a verbatim harness log
(ATIF JSON, Claude Code / Codex / OpenCode sessions, ACP event JSONL) that ends with an added user
message `# Verifier result` and PASS or FAIL. There is no map from file to task and no verifier
detail, so the verdict is a single pass/fail criterion. Output matches fm_traces.py: one §0 .jsonl
per input and an index.json with session_id / task / outcome, which dream.py reads unchanged.

    python eval/frontiermemory/fm_corpus.py --corpus data/corpus/joint-674 --out /tmp/fm/events
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kypp.transcripts import claude_events  # noqa: E402

_OUT = 2000
_VERDICT = re.compile(r"#\s*Verifier result\s*(PASS|FAIL)", re.S)
_TASK_MARKERS = ("Task Description:", "## Task", "# Task")


def _text(v) -> str:
    """Flatten the content shapes these harnesses use (str, block lists, {text}) to plain text."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(t for t in (_text(x) for x in v) if t)
    if isinstance(v, dict):
        for k in ("text", "content", "output", "message"):
            if k in v:
                return _text(v[k])
    return ""


def atif_events(d: dict, sid: str) -> list[dict]:
    ev = lambda p: {"sessionId": sid, "payload": p}
    out, models = [], []
    for s in d.get("steps") or []:
        m = s.get("model_name") or (d.get("agent") or {}).get("model_name")
        if m and m not in models:
            models.append(m)
            out.append(ev({"type": "message_end", "model": m}))
        msg = _text(s.get("message")).strip()
        if msg:
            out.append(ev({"type": "user_message" if s.get("source") == "user" else "assistant_message",
                           "text": msg}))
        results = {r.get("source_call_id"): _text(r.get("content"))
                   for r in ((s.get("observation") or {}).get("results") or [])}
        for i, c in enumerate(s.get("tool_calls") or []):
            res = results.get(c.get("tool_call_id"))
            if res is None and len(results) == 1:
                res = next(iter(results.values()))
            out.append(ev({"type": "tool_call", "name": c.get("function_name", ""),
                           "status": "completed" if res is not None else "unspecified", "title": "",
                           "input": c.get("arguments"), "output": (res or "")[:_OUT]}))
    return out


def opencode_events(d: dict, sid: str) -> list[dict]:
    ev = lambda p: {"sessionId": sid, "payload": p}
    out, models = [], []
    for m in d.get("messages") or []:
        info = m.get("info") or {}
        model = info.get("modelID")
        if model and model not in models:
            models.append(model)
            out.append(ev({"type": "message_end", "model": model}))
        role = info.get("role")
        for p in m.get("parts") or []:
            if p.get("type") == "text" and (p.get("text") or "").strip():
                out.append(ev({"type": "user_message" if role == "user" else "assistant_message",
                               "text": p["text"].strip()}))
            elif p.get("type") == "tool":
                st = p.get("state") or {}
                out.append(ev({"type": "tool_call", "name": p.get("tool", ""),
                               "status": "error" if st.get("status") == "error" else "completed",
                               "title": "", "input": st.get("input"),
                               "output": _text(st.get("output") or st.get("error"))[:_OUT]}))
    return out


def codex_rollout_events(rows: list[dict], sid: str) -> list[dict]:
    """Codex rollouts here carry turns as response_item messages (kypp.transcripts.codex_events reads
    event_msg turns, which these files lack) and tools as function_call / custom_tool_call."""
    ev = lambda p: {"sessionId": sid, "payload": p}
    out, outputs, models = [], {}, []
    for r in rows:
        p = r.get("payload") or {}
        if p.get("type") in ("function_call_output", "custom_tool_call_output"):
            outputs[p.get("call_id")] = _text(p.get("output"))
    for r in rows:
        p = r.get("payload") or {}
        t = p.get("type")
        if r.get("type") in ("session_meta", "turn_context") and p.get("model") and p["model"] not in models:
            models.append(p["model"])
            out.append(ev({"type": "message_end", "model": p["model"]}))
        elif r.get("type") == "response_item" and t == "message" and p.get("role") in ("user", "assistant"):
            text = _text(p.get("content")).strip()
            if text and not text.startswith(("<environment_context>", "# AGENTS.md", "<user_instructions>")):
                out.append(ev({"type": "user_message" if p["role"] == "user" else "assistant_message",
                               "text": text}))
        elif r.get("type") == "response_item" and t in ("function_call", "custom_tool_call"):
            raw = p.get("arguments") if t == "function_call" else p.get("input")
            try:
                inp = json.loads(raw) if isinstance(raw, str) and t == "function_call" else {"input": raw}
            except ValueError:
                inp = {"input": raw}
            res = outputs.get(p.get("call_id"))
            out.append(ev({"type": "tool_call", "name": p.get("name", ""), "title": "", "input": inp,
                           "status": "completed" if res is not None else "unspecified",
                           "output": (res or "")[:_OUT]}))
    return out


def acp_events(rows: list[dict], sid: str) -> list[dict]:
    ev = lambda p: {"sessionId": sid, "payload": p}
    out = []
    for r in rows:
        t = r.get("type")
        if t in ("user_message", "agent_message") and (r.get("text") or "").strip():
            out.append(ev({"type": "user_message" if t == "user_message" else "assistant_message",
                           "text": r["text"].strip()}))
        elif t == "tool_call":
            out.append(ev({"type": "tool_call", "name": r.get("kind") or "", "title": r.get("title") or "",
                           "status": "error" if r.get("status") == "failed" else "completed",
                           "input": {"title": r.get("title")}, "output": _text(r.get("content"))[:_OUT]}))
    return out


def file_events(path: str, sid: str) -> list[dict]:
    """Sniff the harness format and translate. Raises on a shape it doesn't know (loud, not skipped)."""
    if path.endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        if "steps" in d:
            return atif_events(d, sid)
        if "messages" in d:
            return opencode_events(d, sid)
        raise ValueError(f"unknown JSON trajectory shape: {sorted(d)[:6]}")
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    first = rows[0].get("type") if rows else None
    if first == "session_meta" or any(r.get("type") == "response_item" for r in rows[:5]):
        events = codex_rollout_events(rows, sid)
    elif any(r.get("type") in ("user", "assistant", "queue-operation") for r in rows[:5]):
        events, _ = claude_events(path)
    elif any(r.get("type") in ("user_message", "agent_message", "tool_call") for r in rows):
        events = acp_events(rows, sid)
    else:
        raise ValueError(f"unknown JSONL trajectory shape: first type {first!r}")
    for e in events:
        e["sessionId"] = sid
    return events


_CONTEXT_BLOCK = re.compile(r"<(environment_context|user_instructions|system-reminder)>.*?</\1>", re.S)


def core_prompt(text: str) -> str:
    """A user turn without harness boilerplate: context blocks, a preamble before a task marker, and a
    leading "You are a … agent." persona paragraph. Empty when nothing else is left."""
    t = _CONTEXT_BLOCK.sub("", text).strip().strip('"').strip()
    for mark in _TASK_MARKERS:
        if mark in t:
            t = t.split(mark, 1)[1].strip()
            break
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) > 1 and re.match(r"(you are|you're) (an? )?[\w -]*(agent|assistant)\b", paras[0].strip(), re.I):
        t = "\n\n".join(paras[1:])
    return t.strip()


def task_of(events: list[dict]) -> str:
    """The prompt the session was given: its first user turn with content past harness boilerplate."""
    for e in events:
        p = e["payload"]
        if p.get("type") == "user_message":
            t = core_prompt(p["text"])
            if t:
                return t[:1500]
    return ""


def convert(path: str, sid: str) -> tuple[list[dict], dict]:
    raw = open(path, encoding="utf-8").read()
    found = _VERDICT.findall(raw[-4000:].replace("\\n", "\n"))
    if not found:
        raise ValueError("no '# Verifier result' at the end of the file")
    passed = found[-1] == "PASS"
    events = [e for e in file_events(path, sid)
              if not (e["payload"].get("type") == "user_message"
                      and _VERDICT.search(e["payload"].get("text", "")))]
    events.append({"sessionId": sid, "payload": {
        "type": "scored", "grader": "frontiermemory-verifier", "passed": passed,
        "score": 1.0 if passed else 0.0, "feedback": "",
        "criteria": [{"name": "verifier", "passed": passed, "feedback": ""}]}})
    meta = {"session_id": sid, "outcome": "pass" if passed else "fail", "task": task_of(events),
            "n_events": len(events)}
    return events, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    inputs = os.path.join(a.corpus, "inputs")
    index, failed = [], []
    for fn in sorted(os.listdir(inputs)):
        sid = os.path.splitext(fn)[0]
        try:
            events, meta = convert(os.path.join(inputs, fn), sid)
        except (ValueError, json.JSONDecodeError) as e:
            failed.append((fn, str(e)[:160]))
            continue
        with open(os.path.join(a.out, sid + ".jsonl"), "w") as f:
            f.writelines(json.dumps(e) + "\n" for e in events)
        index.append(meta)
    with open(os.path.join(a.out, "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    print(json.dumps({"converted": len(index), "failed": failed,
                      "pass": sum(m["outcome"] == "pass" for m in index)}, indent=1))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
