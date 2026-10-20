#!/usr/bin/env python3
"""eval_episodes.py — do episode quotes get a session's corrections in front of the distiller?

Reads a labelled corpus (eval/episodes/corpus.json: sessions with gold correction/reason quotes and
decoys that must not be picked up) and compares four distiller prompts:

  today        LLMDistiller.render_prompt as shipped: first 40 turns, 400 chars each
  budget       today's format with the 40-turn cap lifted, given as many extra characters as the
               clairic+clef prompt adds (the simpler fix: just show more conversation)
  full         every turn, unclipped: an upper bound that only fits because these sessions are short
  cues         today + kypp.episodes.cue_episodes quotes
  cues+clef    cue sentences reviewed by Clef (kypp.episodes.ClefReview)
  clairic      today + kypp.episodes.ClairicExtractor quotes, unreviewed
  clairic+clef Clairic locates, Clef reviews: what KYPP_EPISODES=clairic ships

Measures, per condition: gold quotes present in the prompt, episode precision and decoy pickups,
prompt size, and (with --distill) whether a real LLM distiller's claims carry each gold lesson's
keywords. Every model response is cached by request hash in --cache, so a rerun without --live
reproduces the numbers offline; --live fills cache misses from Workers AI (CLOUDFLARE_ACCOUNT_ID,
CLOUDFLARE_AI_TOKEN).

    uv run python scripts/eval_episodes.py --corpus eval/episodes/corpus.json \
        --cache eval/episodes/cache.json --out eval/episodes/results.json [--live] [--distill]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kypp import episodes as ep  # noqa: E402
from kypp.distill import LLMDistiller, _clip, build_trace  # noqa: E402

DISTILL_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
CLAIRIC_USD_PER_MTOK = 0.10  # input tokens only, per the Workers AI model pages (2026-10)
CLEF_USD_PER_MTOK = 0.24


class Cache:
    def __init__(self, path: str | None, live: bool):
        self.path, self.live = path, live
        self.data = json.load(open(path)) if path and os.path.exists(path) else {}
        self.misses = 0

    def get(self, key_obj, fetch):
        key = hashlib.sha256(json.dumps(key_obj, sort_keys=True).encode()).hexdigest()
        if key not in self.data:
            if not self.live:
                self.misses += 1
                raise KeyError("cache miss (rerun with --live)")
            t0 = time.monotonic()
            value = fetch()
            self.data[key] = {"value": value, "seconds": round(time.monotonic() - t0, 3)}
            if self.path:
                json.dump(self.data, open(self.path, "w"), indent=0, sort_keys=True)
        return self.data[key]


def _post(model: str, body: dict) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/run/{model}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {os.environ['CLOUDFLARE_AI_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        env = json.loads(r.read())
    if not env.get("success", True):
        raise RuntimeError(str(env.get("errors"))[:300])
    return env["result"]


def clairic_runner(cache: Cache, log: list):
    def run(state, fields):
        hit = cache.get({"m": "clairic", "state": state, "fields": fields},
                        lambda: _post("@cf/cloudflare/clairic", {"model": "clairic", "state": state, "fields": fields}))
        res = hit["value"]
        log.append({"seconds": hit["seconds"], "usage": res.get("usage", {}), "truncated": res.get("truncated")})
        if res.get("truncated"):
            raise ep.Truncated("state truncated")
        return res.get("answers") or {}
    return run


def clef_runner(cache: Cache, log: list):
    def run(state, questions):
        hit = cache.get({"m": "clef", "state": state, "questions": questions},
                        lambda: _post("@cf/cloudflare/clef", {"model": "clef", "state": state, "questions": questions}))
        log.append({"seconds": hit["seconds"], "usage": hit["value"].get("usage", {}), "truncated": False})
        return hit["value"].get("answers") or {}
    return run


def distill_complete(cache: Cache):
    def complete(prompt: str) -> str:
        body = {"messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 1500}
        res = cache.get({"m": DISTILL_MODEL, "body": body}, lambda: _post(DISTILL_MODEL, body))["value"]
        if "choices" in res:
            return res["choices"][0]["message"]["content"] or ""
        return res.get("response") or ""
    return complete


def events_of(session: dict) -> list[dict]:
    return [{"sessionId": session["id"], "payload": {"type": f"{t['role']}_message", "text": t["text"]}}
            for t in session["turns"]]


def _span(session: dict, item: dict) -> tuple[int, int, int]:
    text = session["turns"][item["turn"]]["text"]
    i = text.index(item["quote"])
    return item["turn"], i, i + len(item["quote"])


def covered(span, eps) -> bool:
    turn, s, e = span
    return any(x.event == turn and min(x.end, e) - max(x.start, s) >= 0.5 * (e - s) for x in eps)


_norm = lambda s: " ".join(s.lower().split())  # noqa: E731


def budget_prompt(trace, extra: int) -> str:
    """Today's prompt with the 40-turn cap lifted, the conversation allowed `extra` more characters."""
    base = LLMDistiller(lambda p: "[]").render_prompt(trace)
    head, _, rest = base.partition("## Conversation\n")
    conv, _, actions = rest.partition("## Actions")
    limit, lines = len(conv) + extra, []
    for turn in trace.turns:
        line = f"[{turn.get('role', '?')}] {_clip(turn.get('text', ''), 400)}\n"
        if sum(map(len, lines)) + len(line) > limit:
            break
        lines.append(line)
    return head + "## Conversation\n" + "".join(lines) + "## Actions" + actions


def full_prompt(trace) -> str:
    base = LLMDistiller(lambda p: "[]").render_prompt(trace)
    head, _, rest = base.partition("## Conversation\n")
    _, _, actions = rest.partition("## Actions")
    conv = "".join(f"[{t.get('role', '?')}] {t.get('text', '')}\n" for t in trace.turns)
    return head + "## Conversation\n" + conv + "## Actions" + actions


def lesson_hit(gold: dict, drafts) -> bool:
    text = " ".join(f"{d.subject} {d.content}".lower() for d in drafts)
    return all(k.lower() in text for k in gold["keywords"])


def steering_leak(decoy: dict, drafts) -> bool:
    words = [w for w in re.findall(r"[a-z0-9./:_-]{4,}", decoy["quote"].lower())]
    text = " ".join(f"{d.subject} {d.content}".lower() for d in drafts)
    return bool(words) and sum(w in text for w in words) >= max(2, len(words) // 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--cache")
    ap.add_argument("--out")
    ap.add_argument("--live", action="store_true", help="call Workers AI on cache misses")
    ap.add_argument("--distill", action="store_true", help="also run the LLM distiller per condition")
    args = ap.parse_args(argv)

    corpus = json.load(open(args.corpus))
    cache = Cache(args.cache, args.live)
    calls: list = []
    clef_calls: list = []
    clairic = ep.ClairicExtractor(clairic_runner(cache, calls))
    review = lambda found: ep.ClefReview(lambda _t: found, clef_runner(cache, clef_calls))  # noqa: E731
    conds = ["today", "budget", "full", "cues", "cues+clef", "clairic", "clairic+clef"]
    located = ("cues", "cues+clef", "clairic", "clairic+clef")
    rows = []
    for s in corpus:
        trace = build_trace(events_of(s), task="(eval session)")
        found = {"cues": ep.cue_episodes(trace), "clairic": clairic(trace)}
        found["cues+clef"] = review(found["cues"])(trace)
        found["clairic+clef"] = review(found["clairic"])(trace)
        prompts = {}
        for c in ("today", *located):
            trace.episodes = found.get(c) or []
            prompts[c] = LLMDistiller(lambda p: "[]").render_prompt(trace)
        trace.episodes = []
        prompts["budget"] = budget_prompt(trace, len(prompts["clairic+clef"]) - len(prompts["today"]))
        prompts["full"] = full_prompt(trace)
        gold = [(g, _span(s, g)) for g in s["gold"]]
        decoys = [(d, _span(s, d)) for d in s.get("decoys", [])]
        row = {"id": s["id"], "category": s["category"], "gold": len(gold), "turns": len(s["turns"]),
               "failed": [c for c in located if found[c] is None], "conditions": {}}
        for c in conds:
            eps = found.get(c) or []
            norm = _norm(prompts[c])
            r = {"prompt_chars": len(prompts[c]),
                 "gold_in_prompt": sum(_norm(g["quote"]) in norm or covered(sp, eps) for g, sp in gold)}
            if c in found:
                r.update(episodes=len(eps),
                         gold_found=sum(covered(sp, eps) for _, sp in gold),
                         unmatched=sum(not any(t == x.event and min(x.end, b) > max(x.start, a)
                                               for _, (t, a, b) in gold) for x in eps),
                         decoys_hit={k: sum(covered(sp, eps) for d, sp in decoys if d["kind"] == k)
                                     for k in ("steering", "lookalike", "assistant")})
            row["conditions"][c] = r
        if args.distill:
            for c in conds:
                try:
                    drafts = LLMDistiller.parse(distill_complete(cache)(prompts[c]))
                except KeyError:
                    raise
                except Exception as e:  # unparseable output is a measured failure, not a crash
                    drafts = []
                    row["conditions"][c]["distill_error"] = str(e)[:120]
                row["conditions"][c].update(
                    claims=len(drafts),
                    lessons=sum(lesson_hit(g, drafts) for g, _ in gold),
                    steering_leaks=sum(steering_leak(d, drafts) for d, _ in decoys if d["kind"] == "steering"))
        rows.append(row)

    summary = summarize(rows, calls, clef_calls, conds, args.distill)
    out = {"distill_model": DISTILL_MODEL if args.distill else None, "summary": summary, "sessions": rows}
    if args.out:
        json.dump(out, open(args.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))
    return 0


def summarize(rows, calls, clef_calls, conds, distilled):
    tot_gold = sum(r["gold"] for r in rows)
    by_cat: dict = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    out = {"sessions": len(rows), "gold": tot_gold,
           "failed_sessions": {c: sum(c in r["failed"] for r in rows) for c in conds}, "conditions": {}}
    for c in conds:
        cs = [r["conditions"][c] for r in rows]
        d = {"gold_in_prompt": sum(x["gold_in_prompt"] for x in cs),
             "mean_prompt_chars": round(sum(x["prompt_chars"] for x in cs) / len(cs)),
             "gold_in_prompt_by_category": {k: f"{sum(x['conditions'][c]['gold_in_prompt'] for x in v)}/{sum(x['gold'] for x in v)}"
                                            for k, v in sorted(by_cat.items())}}
        if "episodes" in cs[0]:
            n = sum(x["episodes"] for x in cs)
            d.update(episodes=n, gold_found=sum(x["gold_found"] for x in cs),
                     unmatched_episodes=sum(x["unmatched"] for x in cs),
                     episodes_on_no_gold_sessions=sum(x["episodes"] for r, x in zip(rows, cs) if not r["gold"]),
                     decoys_hit={k: sum(x["decoys_hit"][k] for x in cs) for k in ("steering", "lookalike", "assistant")})
        if distilled:
            d.update(lessons=sum(x.get("lessons", 0) for x in cs),
                     claims=sum(x.get("claims", 0) for x in cs),
                     distill_errors=sum("distill_error" in x for x in cs),
                     steering_leaks=sum(x.get("steering_leaks", 0) for x in cs),
                     lessons_by_category={k: f"{sum(x['conditions'][c].get('lessons', 0) for x in v)}/{sum(x['gold'] for x in v)}"
                                          for k, v in sorted(by_cat.items())})
        out["conditions"][c] = d
    for name, log, usd in (("clairic_calls", calls, CLAIRIC_USD_PER_MTOK), ("clef_calls", clef_calls, CLEF_USD_PER_MTOK)):
        if log:
            secs = sorted(x["seconds"] for x in log)
            toks = sum(x["usage"].get("input_tokens", 0) for x in log)
            out[name] = {"calls": len(log), "median_seconds": secs[len(secs) // 2], "max_seconds": secs[-1],
                         "input_tokens": toks, "usd": round(toks * usd / 1e6, 4),
                         "truncated": sum(bool(x["truncated"]) for x in log)}
    return out


if __name__ == "__main__":
    sys.exit(main())
