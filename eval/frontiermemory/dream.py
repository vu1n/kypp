#!/usr/bin/env python3
"""The "dream" phase for FrontierMemory: §0 logs (from fm_traces.py) → a kypp store.

Runs kypp's own write path unchanged: build_trace → LLMDistiller (+ episodes when configured) →
distill_session → consolidate (the corroboration gate that promotes candidates). The only addition is
the `complete` backend: the Anthropic API with a response cache, so a rerun is offline and the
distillation can fan out (store writes stay on one thread).

    tumbler run -- uv run --with anthropic python eval/frontiermemory/dream.py \
        --logs /tmp/fm/events --db /tmp/fm/kypp.db --cache eval/frontiermemory/dream_cache.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kypp import arbiter  # noqa: E402
from kypp.distill import LLMDistiller, build_trace, distill_session, read_log  # noqa: E402
from kypp.store import store_from_env  # noqa: E402

PROJECT = "frontiermemory"


class CachedComplete:
    """prompt → completion through the Anthropic API, memoized by prompt hash; tracks token usage."""

    def __init__(self, path: str, model: str, effort: str, live: bool):
        self.path, self.model, self.effort, self.live = path, model, effort, live
        self.cache = json.load(open(path)) if os.path.exists(path) else {}
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "hits": 0}
        self.lock = threading.Lock()
        self.client = None

    def __call__(self, prompt: str) -> str:
        key = hashlib.sha256(f"{self.model}|{self.effort}|{prompt}".encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                self.usage["hits"] += 1
                return self.cache[key]["text"]
        if not self.live:
            raise RuntimeError("cache miss (rerun with --live)")
        if self.client is None:
            import anthropic
            self.client = anthropic.Anthropic()
        r = self.client.messages.create(model=self.model, max_tokens=16000,
                                        output_config={"effort": self.effort},
                                        messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if b.type == "text")
        with self.lock:
            self.cache[key] = {"text": text, "in": r.usage.input_tokens, "out": r.usage.output_tokens}
            self.usage["calls"] += 1
            self.usage["input_tokens"] += r.usage.input_tokens
            self.usage["output_tokens"] += r.usage.output_tokens
        return text

    def save(self) -> None:
        with open(self.path, "w") as f:
            json.dump(self.cache, f, indent=0, sort_keys=True)


class Precomputed:
    """Hands distill_session drafts that were distilled off-thread, so the store path is unchanged."""

    def __init__(self, drafts):
        self.drafts = drafts

    def distill(self, trace):
        return self.drafts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    os.environ["KYPP_MEMORY_DB"] = a.db
    store = store_from_env()
    complete = CachedComplete(a.cache, a.model, a.effort, a.live)
    distiller = LLMDistiller(complete)
    index = json.load(open(os.path.join(a.logs, "index.json")))

    def drafts(meta):
        events = read_log(os.path.join(a.logs, meta["session_id"] + ".jsonl"))
        trace = build_trace(events, task=meta["task"])
        return events, distiller.distill(trace)

    n_claims, failed = 0, []
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(drafts, m): m for m in index}
        for fut in cf.as_completed(futs):
            meta = futs[fut]
            try:
                events, ds = fut.result()
            except Exception as e:  # noqa: BLE001 — report and keep going; one bad trace is not the run
                failed.append((meta["session_id"], f"{type(e).__name__}: {str(e)[:160]}"))
                continue
            n_claims += len(distill_session(events, store, project=PROJECT, task=meta["task"],
                                            distiller=Precomputed(ds)))
    complete.save()
    report = arbiter.consolidate(store, project=PROJECT)
    print(json.dumps({"traces": len(index), "claims": n_claims, "failed": failed,
                      "consolidate": report, "usage": complete.usage}, indent=1, default=str))


if __name__ == "__main__":
    main()
