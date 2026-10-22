#!/usr/bin/env python3
"""FrontierMemory source trajectories → kypp §0 events.

FrontierMemory (frontiermemory.com, Letta) publishes each task's source trajectories as HTML trace
pages: the run (user / assistant / reasoning / tool call / tool result steps), the verifier output,
and a pass/fail outcome. This is the adapter's front end: it scrapes those pages into a local cache
and translates each one into the same §0 event stream `kypp.wire.capture_events` consumes, so the
distiller and store run unchanged. The 446 distractor trajectories the benchmark mentions are not
published; only the 219 linked from task pages are.

    python eval/frontiermemory/fm_traces.py fetch  --cache DIR        # task pages + traces (HTML)
    python eval/frontiermemory/fm_traces.py events --cache DIR --out DIR   # one §0 .jsonl per trace

The cache is the benchmark's data, so it stays out of git.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import html
import json
import os
import re
import urllib.request

SITE = "https://frontiermemory.com"
_OUT = 2000  # per-action output cap, as kypp.transcripts


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def task_slugs(index_html: str) -> list[str]:
    return sorted(set(re.findall(r'href="/tasks/([^"/]+)/"', index_html)))


def fetch(cache: str) -> None:
    os.makedirs(os.path.join(cache, "tasks"), exist_ok=True)
    os.makedirs(os.path.join(cache, "traces"), exist_ok=True)
    urls = []
    for slug in task_slugs(_get(f"{SITE}/tasks/")):
        page = _get(f"{SITE}/tasks/{slug}/")
        with open(os.path.join(cache, "tasks", f"{slug}.html"), "w") as f:
            f.write(page)
        urls += sorted(set(re.findall(r'href="(/traces/[^"]+)"', page)))

    def one(path: str) -> None:
        dst = os.path.join(cache, "traces", path.strip("/").removeprefix("traces/").replace("/", "__") + ".html")
        if not os.path.exists(dst):
            with open(dst, "w") as f:
                f.write(_get(SITE + path))

    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(one, urls))
    print(f"{len(urls)} traces cached under {cache}")


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _sections(body: str) -> list[tuple[str, str, str]]:
    """(kind, tool name, text) per <section> of one trace step, in order."""
    out = []
    for m in re.finditer(r'<section(?: class="([^"]*)")?>(.*?)</section>', body, re.S):
        cls, inner = m.group(1) or "", m.group(2)
        name = re.search(r"<strong>(.*?)</strong>", inner)
        pre = re.search(r"<pre>(.*?)</pre>", inner, re.S)
        out.append((cls, _text(name.group(1)) if name else "", _text(pre.group(1)) if pre else ""))
    return out


def _tool_input(text: str) -> dict:
    """The page renders a call's input as `key:\\nvalue` blocks; recover the dict best-effort."""
    parts = re.split(r"^([A-Za-z_][\w]*):\n", text, flags=re.M)
    if len(parts) < 3:
        return {"input": text}
    return {k: v.strip() for k, v in zip(parts[1::2], parts[2::2])}


_PYTEST = re.compile(r"^(\S+::\S+) (PASSED|FAILED|ERROR)", re.M)


def verdict(grader_text: str, passed: bool) -> dict:
    """A §0 `scored` payload. pytest output decomposes into per-test criteria; anything else is one
    criterion carrying the output's tail (where verifiers print what went wrong)."""
    crit = []
    for name, status in dict(_PYTEST.findall(grader_text)).items():
        fb = ""
        if status != "PASSED":
            m = re.search(rf"^(?:FAILED|ERROR) {re.escape(name)} - (.*)$", grader_text, re.M)
            fb = m.group(1) if m else ""
        crit.append({"name": name, "passed": status == "PASSED", "feedback": fb})
    if not crit:
        crit = [{"name": "verifier", "passed": passed, "feedback": grader_text[-600:]}]
    return {"type": "scored", "grader": "frontiermemory-verifier", "passed": passed,
            "score": 1.0 if passed else 0.0, "feedback": grader_text[-600:], "criteria": crit}


def parse_trace(page: str, sid: str) -> tuple[list[dict], dict]:
    """One trace page → (§0 events, meta). Steps come from the full (not compact) view."""
    model_line = _text((re.search(r'<p class="trace-model-line">(.*?)</p>', page, re.S) or [None, ""])[1])
    model, _, harness = model_line.partition(" · ")
    outcome = (re.search(r'class="trace-outcome (\w+)"', page) or [None, ""])[1]
    full = page.split('class="trace-full-view"', 1)[-1].split('class="trace-compact-view"', 1)[0]
    ev = lambda p: {"sessionId": sid, "payload": p}
    events = [ev({"type": "message_end", "model": model})] if model else []
    task = ""
    for m in re.finditer(r'<article class="trace-step role-(\w+)">(.*?)</article>', full, re.S):
        role, body = m.group(1), m.group(2)
        pending = None
        for cls, name, text in _sections(body):
            if "trace-tool call" in cls:
                pending = ev({"type": "tool_call", "name": name, "status": "unspecified", "title": "",
                              "input": _tool_input(text), "output": ""})
                events.append(pending)
            elif "trace-tool result" in cls:
                if pending is not None:
                    pending["payload"]["output"] = text[:_OUT]
                    pending["payload"]["status"] = "completed"
                    pending = None
            elif "reasoning" in cls or not text:
                continue  # reasoning is the model's scratch, not something a later run can act on
            elif role == "user":
                task = task or text
                events.append(ev({"type": "user_message", "text": text}))
            else:
                events.append(ev({"type": "assistant_message", "text": text}))
    grader = re.search(r'id="panel-grader"[^>]*>(.*?)</section>', page, re.S)
    grader_text = _text(re.sub(r"<br\s*/?>", "\n", grader.group(1))) if grader else ""
    events.append(ev(verdict(grader_text, outcome == "pass")))
    meta = {"session_id": sid, "model": model, "harness": harness, "outcome": outcome,
            "task": task[:400], "n_events": len(events)}
    return events, meta


def to_events(cache: str, out: str) -> None:
    os.makedirs(out, exist_ok=True)
    index = []
    for fn in sorted(os.listdir(os.path.join(cache, "traces"))):
        sid = fn.removesuffix(".html")
        with open(os.path.join(cache, "traces", fn)) as f:
            events, meta = parse_trace(f.read(), sid)
        meta["fm_task"] = sid.split("__", 1)[0]
        with open(os.path.join(out, sid + ".jsonl"), "w") as f:
            f.writelines(json.dumps(e) + "\n" for e in events)
        index.append(meta)
    with open(os.path.join(out, "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    print(f"{len(index)} logs → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "events"])
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    fetch(a.cache) if a.cmd == "fetch" else to_events(a.cache, a.out)
