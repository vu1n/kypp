"""Compatibility import for the ratified pure kernel, now shared by local adapters.

The portable vectors specify outcomes independently. Database conformance and the
Worker port are tested separately; these imports are not an independent oracle.
"""
from kypp.claim_machine import (
    AUTHORITY, RETIRED, Accept, Action, Actor, BeginCheck, Claim, Command,
    CompleteCheck, Correct, Event, Evidence, Propose, Receipt, Refused, Reject,
    Stamp, State, Token, transition,
)
