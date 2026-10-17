"""Proposed executable contract, NOT imported by the Kypp runtime.

One already-authorized arbitration group; adapters own authentication, routing,
source/exposure stamps, clocks, SQL and shell execution. See docs/claim-state-machine.md.
No side effects or database dependencies. All rejected commands leave State intact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json


AUTHORITY = {"agent": 0, "verified": 1, "human": 2}
RETIRED = {"rejected", "superseded"}


class Refused(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


@dataclass(frozen=True)
class Actor:
    principal: str
    role: str  # authenticated adapter capability: agent | operator | maintenance


@dataclass(frozen=True)
class Stamp:
    """Authenticated adapter provenance, separate from the untrusted action."""
    source: str | None = None
    echo: bool = False


@dataclass(frozen=True)
class Token:
    claim_id: str
    claim_revision: int
    attempt: int
    verifier_digest: str
    head_revision: int


@dataclass(frozen=True)
class Evidence:
    claim_id: str
    claim_revision: int
    principal: str
    source: str


@dataclass(frozen=True)
class Claim:
    id: str
    content: str
    principal: str
    source: str | None = None
    echo: bool = False
    based_on: int = 0  # current-answer generation, not a timestamp or nullable head ID
    previous: str | None = None
    status: str = "candidate"
    authority: str = "agent"
    acceptance: str | None = None
    revision: int = 1
    successor: str | None = None
    verifier: str | None = None
    attempt: int = 0
    pending_check: Token | None = None
    check: str | None = None  # passed | failed | inconclusive; None = not checked

    @property
    def usable(self) -> bool:
        # Authorization is a separate adapter predicate, never implied by this.
        return self.status == "accepted" and (self.verifier is None or self.check == "passed")


@dataclass(frozen=True)
class Propose:
    claim_id: str
    content: str
    previous: str | None = None
    verifier: str | None = None


@dataclass(frozen=True)
class Accept:
    claim_id: str
    reason: str = "explicit"  # explicit | recurrence; other reasons have dedicated commands
    support: tuple[str, ...] = ()
    agreement: str = "unknown"  # exact | agrees | unknown | conflicts


@dataclass(frozen=True)
class Correct:
    claim_id: str
    content: str
    verifier: str | None = None


@dataclass(frozen=True)
class Reject:
    claim_id: str
    reason: str | None = None


@dataclass(frozen=True)
class BeginCheck:
    claim_id: str


@dataclass(frozen=True)
class CompleteCheck:
    token: Token
    outcome: str  # passed | failed | inconclusive


Action = Propose | Accept | Correct | Reject | BeginCheck | CompleteCheck


@dataclass(frozen=True)
class Command:
    id: str
    expected_revision: int
    action: Action


@dataclass(frozen=True)
class Event:
    revision: int
    command_id: str
    principal: str
    kind: str
    claim_id: str
    evidence: tuple[Evidence, ...] = ()
    reason: str | None = None
    check_token: Token | None = None
    check_outcome: str | None = None


@dataclass(frozen=True)
class Receipt:
    principal: str
    command_id: str
    fingerprint: str
    revision: int
    claim_id: str
    token: Token | None = None


@dataclass(frozen=True)
class State:
    revision: int = 0
    head: str | None = None
    head_revision: int = 0
    claims: tuple[Claim, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    events: tuple[Event, ...] = ()

    def get(self, claim_id: str) -> Claim:
        for claim in self.claims:
            if claim.id == claim_id:
                return claim
        raise Refused("not_found")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def amend(state: State, *claims: Claim) -> State:
    changed = {c.id: c for c in claims}
    return replace(state, claims=tuple(changed.get(c.id, c) for c in state.claims))


def live(claim: Claim) -> None:
    require(claim.status not in RETIRED, "retired")


def insert(state: State, claim: Claim) -> State:
    require(bool(claim.id) and bool(claim.content.strip()), "invalid")
    require(all(c.id != claim.id for c in state.claims), "id_exists")
    if claim.previous is not None:
        state.get(claim.previous)
    return replace(state, claims=state.claims + (claim,))


def accept(state: State, claim: Claim, reason: str, support: tuple[str, ...] = ()) -> State:
    require(claim.status == "candidate", "not_candidate")
    retired = []
    if state.head is not None:
        head = state.get(state.head)
        # A temporarily unconfirmed head still owns its authority. No weaker fallback.
        require(AUTHORITY[claim.authority] >= AUTHORITY[head.authority], "lower_authority")
        retired.append(replace(head, status="superseded", successor=claim.id, revision=head.revision + 1))
    for cid in support:
        if cid != claim.id:
            other = state.get(cid)
            retired.append(replace(other, status="superseded", successor=claim.id, revision=other.revision + 1))
    accepted = replace(claim, status="accepted", acceptance=reason, revision=claim.revision + 1)
    return replace(amend(state, accepted, *retired), head=claim.id, head_revision=state.revision + 1)


def transition(state: State, actor: Actor, command: Command, *, stamp: Stamp | None = None,
               k: int = 2) -> tuple[State, Receipt]:
    """Reference atomic commit. An adapter must supply equivalent database atomicity.

    The exact action type participates in the retry fingerprint. Receipts survive
    subsequent mutations; a retry returns its original result, not today's state.
    """
    require(actor.role in {"agent", "operator", "maintenance"} and bool(actor.principal), "forbidden")
    require(bool(command.id) and k >= 2, "invalid")
    fingerprint = digest(json.dumps({"role": actor.role, "action": type(command.action).__name__,
                                     "command": asdict(command)}, sort_keys=True))
    for receipt in state.receipts:
        if (receipt.principal, receipt.command_id) == (actor.principal, command.id):
            require(receipt.fingerprint == fingerprint, "idempotency_conflict")
            return state, receipt
    require(command.expected_revision == state.revision, "stale")
    action = command.action
    token = None
    evidence: tuple[Evidence, ...] = ()

    if isinstance(action, Propose):
        require(actor.role in {"agent", "operator"}, "forbidden")
        require(action.verifier is None or (actor.role == "operator" and bool(action.verifier.strip())), "forbidden")
        require(isinstance(stamp, Stamp), "missing_stamp")
        # Relaying the same original observation cannot make it fresh again.
        generation = min((c.based_on for c in state.claims if stamp.source is not None
                          and c.source == stamp.source), default=state.head_revision)
        echo = stamp.echo or any(c.echo for c in state.claims if stamp.source is not None and c.source == stamp.source)
        claim = Claim(action.claim_id, action.content, actor.principal, source=stamp.source,
                      echo=echo, based_on=generation, previous=action.previous, verifier=action.verifier)
        next_state = insert(state, claim)
    elif isinstance(action, Correct):
        require(actor.role == "operator", "forbidden")
        require(action.verifier is None or bool(action.verifier.strip()), "invalid")
        claim = Claim(action.claim_id, action.content, actor.principal, authority="human",
                      based_on=state.head_revision, previous=state.head, verifier=action.verifier)
        replaced = tuple(c.id for c in state.claims if c.status not in RETIRED and c.id != state.head)
        next_state = accept(insert(state, claim), claim, "correction", replaced)
    elif isinstance(action, Accept):
        claim = state.get(action.claim_id)
        live(claim)
        if action.reason == "explicit":
            require(actor.role == "operator", "forbidden")
            require(not action.support, "invalid")
        elif action.reason == "recurrence":
            require(actor.role == "maintenance", "forbidden")
            require(len(set(action.support)) == len(action.support) and claim.id in action.support, "invalid_support")
            members = tuple(state.get(cid) for cid in action.support)
            require(all(c.status == "candidate" and c.authority == "agent" and not c.echo
                        and c.source for c in members), "invalid_support")
            # A newly observed agreement can reuse older unretired support. Old
            # alternatives alone cannot automatically oscillate the current answer.
            require(any(c.based_on == state.head_revision for c in members), "fresh_support_required")
            require(len({c.source for c in members}) >= k, "insufficient_recurrence")
            require(action.agreement in {"exact", "agrees"}, "agreement_required")
            if action.agreement == "exact":
                require(len({" ".join(c.content.split()) for c in members}) == 1, "agreement_required")
            # 'agrees' is a typed external judgment, not a theorem about language.
            evidence = tuple(Evidence(c.id, c.revision, c.principal, c.source) for c in members)
        else:
            raise Refused("invalid")
        next_state = accept(state, claim, action.reason, action.support)
    elif isinstance(action, Reject):
        claim = state.get(action.claim_id)
        require(actor.role == "operator" or (actor.role == "maintenance" and claim.status == "candidate"
                                             and claim.authority == "agent"), "forbidden")
        live(claim)
        next_state = amend(state, replace(claim, status="rejected", revision=claim.revision + 1))
        if state.head == claim.id:
            next_state = replace(next_state, head=None, head_revision=state.revision + 1)
    elif isinstance(action, BeginCheck):
        require(actor.role == "operator", "forbidden")
        claim = state.get(action.claim_id)
        live(claim)
        require(claim.verifier is not None, "no_verifier")
        checked = replace(claim, revision=claim.revision + 1, attempt=claim.attempt + 1)
        token = Token(claim.id, checked.revision, checked.attempt, digest(claim.verifier), state.head_revision)
        next_state = amend(state, replace(checked, pending_check=token))
    elif isinstance(action, CompleteCheck):
        require(actor.role == "operator", "forbidden")
        claim = state.get(action.token.claim_id)
        live(claim)
        require(claim.verifier is not None, "no_verifier")
        require(action.token == claim.pending_check and action.token.claim_revision == claim.revision
                and action.token.verifier_digest == digest(claim.verifier), "stale_attempt")
        require(action.outcome in {"passed", "failed", "inconclusive"}, "invalid")
        checked = replace(claim, check=action.outcome, revision=claim.revision + 1, pending_check=None)
        passed_authority = "human" if claim.authority == "human" else "verified"
        replaceable = action.token.head_revision == state.head_revision and (
            state.head is None or AUTHORITY[passed_authority] >= AUTHORITY[state.get(state.head).authority])
        if action.outcome == "passed" and checked.status == "candidate" and replaceable:
            next_state = accept(state, replace(checked, authority=passed_authority), "verification")
        else:
            # A candidate's stale/blocked pass records evidence, not authority.
            if action.outcome == "passed" and checked.status == "accepted":
                checked = replace(checked, authority=passed_authority)
            next_state = amend(state, checked)
    else:
        raise Refused("invalid")

    cid = claim.id
    revision = state.revision + 1
    receipt = Receipt(actor.principal, command.id, fingerprint, revision, cid, token)
    reason = action.reason if isinstance(action, (Accept, Reject)) else None
    event = Event(revision, command.id, actor.principal, type(action).__name__, cid, evidence, reason,
                  action.token if isinstance(action, CompleteCheck) else token,
                  action.outcome if isinstance(action, CompleteCheck) else None)
    next_state = replace(next_state, revision=revision, receipts=state.receipts + (receipt,),
                         events=state.events + (event,))
    return next_state, receipt
