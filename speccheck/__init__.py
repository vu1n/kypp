"""Advisory spec-consistency checker.

Structural reference checks plus a hand-written registry of signature and schema
rules. Deterministic: no model. Never writes ``.brief/`` or SIGNOFF, and never
treats a PR's own spec edits as the rules the code is judged against.
"""

from .engine import run_check

__all__ = ["run_check"]
