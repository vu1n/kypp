"""Optional System One gate: typed, calibrated yes/no from a decision model via the TypeSafe API.

A System One model (Jev, Clef) is not an LLM: it takes a `state` plus typed questions and returns
calibrated probabilities, never text. kypp uses it only to filter what enters memory; with no
model every caller gets None ("no opinion") and keeps today's behavior.

Optional twice over: it needs `typesafe-sdk` (`kypp[s1]`) and a key. The SDK's own env vars
configure it, so any TypeSafe-API endpoint works: TYPESAFE_API_KEY, TYPESAFE_BASE_URL
(e.g. https://openrouter.ai/api), TYPESAFE_DEFAULT_MODEL. KYPP_S1=off disables it.
Same client shape as brief's `brief.s1`, kept separate so kypp has no brief dependency.
"""
from __future__ import annotations

import math
import os
from typing import Any, Mapping, Protocol


class Client(Protocol):
    def system_one(self, state: Any, questions: Mapping[str, Any]) -> Any: ...


def client(env: Mapping[str, str] | None = None) -> Client | None:
    env = os.environ if env is None else env
    if env.get("KYPP_S1", "").lower() in {"0", "off", "false", "no"} or not env.get("TYPESAFE_API_KEY"):
        return None
    try:
        from typesafe_sdk import TypeSafeClient
        return TypeSafeClient()
    except Exception:  # SDK not installed, or it rejected its config
        return None


def noul(state: Any, question: Mapping[str, Any], *, via: Client | None) -> float | None:
    """P(yes) for one noul question, or None for "no opinion" (no client, a failed call, or an
    answer that isn't a finite probability). Callers must never read None as "no"."""
    if via is None:
        return None
    try:
        raw = via.system_one(state, {"q": dict(question)}).answers.get("q")
        if getattr(raw, "type", None) != "noul":
            return None
        p = float(raw.noul)
    except Exception:
        return None
    return p if math.isfinite(p) and 0.0 <= p <= 1.0 else None


def keep_question() -> dict:
    """noul over a distilled draft: is it worth remembering? Criteria describe situations,
    not degrees (TypeSafe guidance)."""
    return {
        "type": "noul",
        "instructions": (
            "The state holds a lesson distilled from one coding-agent session, and the task. "
            "Would a later agent working on this repository need this lesson, and be unable to "
            "get it by reading the current code or docs?"
        ),
        "criteria": {
            "true": "A durable preference, process, pitfall or in-flight decision that holds "
                    "beyond this session and that the code does not show.",
            "false": "A play-by-play step, a fact the code already states, a one-off "
                     "environment quirk, or something only true for this run.",
        },
    }
