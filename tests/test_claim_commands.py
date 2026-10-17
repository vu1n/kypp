"""Real local SQL transactions, not only pure-state tests. No live services."""
from dataclasses import asdict, replace
from itertools import permutations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import re
import json
import sqlite3
from threading import Barrier, Lock
import unittest
from unittest.mock import patch

from kypp.backends import LibsqlStore, SqliteFtsStore
from kypp.claim_commands import Group, LocalClaimCommands, SCHEMA, NORMALIZE_DDL, NORMALIZE_DATA, SAVE, encoded
from kypp.claim_machine import (
    Actor, BeginCheck, Command, CompleteCheck, Correct, Propose, Receipt, Refused, Reject, Stamp, State, transition,
)
from kypp.store import MemoryStore
from claim_trace_oracle import vectors

OWNER = Actor("owner", "operator")
GROUP = Group("port", "project", "p")
SAME_RECEIPT = Receipt("owner", "same", "e390067f0ecee3e8253bb6eca0b17e8bc5e0ad24076915fe98da5b6f0d641694", 1, "a", None)


class CommandContract:
    backend_type = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = self.open("main")
        self.commands = LocalClaimCommands(self.backend)

    def open(self, name):
        backend = self.backend_type(str(Path(self.tmp.name) / f"{name}.db"))
        self.addCleanup(backend.db.close)
        return backend

    def test_portable_vectors_full_persisted_state_and_receipts(self):
        for n, case in enumerate(vectors()):
            with self.subTest(case=case["name"]):
                commands = LocalClaimCommands(self.open(f"trace-{n}"))
                for step in case["steps"]:
                    if "error" in step:
                        before = commands.read(GROUP)
                        with self.assertRaises(Refused) as caught:
                            commands.execute(GROUP, step["actor"], step["command"], stamp=step["stamp"])
                        self.assertEqual(caught.exception.code, step["error"])
                        self.assertEqual(commands.read(GROUP), before)
                    else:
                        receipt = commands.execute(GROUP, step["actor"], step["command"], stamp=step["stamp"])
                        self.assertEqual(receipt, step["receipt"])
                    self.assertEqual(commands.read(GROUP), step["state"])

    def test_reopen_and_retry_after_later_mutation(self):
        command = Command("create", 0, Propose("a", "café 日本語"))
        original = self.commands.execute(GROUP, OWNER, command, stamp=Stamp("first"))
        self.commands.execute(GROUP, OWNER, Command("fix", 1, Correct("b", "right")))
        reopened = LocalClaimCommands(self.open("main"))
        self.assertEqual(reopened.execute(GROUP, OWNER, command, stamp=Stamp("different")), original)
        self.assertEqual(reopened.read(GROUP).revision, 2)
        self.assertEqual(reopened.read(GROUP).get("a").source, "first")
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            reopened.execute(GROUP, OWNER, replace(command, action=Propose("a", "different")), stamp=Stamp())

    def test_group_and_claim_identity_are_global_and_exact(self):
        create = Command("create", 0, Propose("a", "lesson"))
        self.commands.execute(GROUP, OWNER, create, stamp=Stamp())
        other = Group("port", "project", "q")
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            self.commands.execute(other, OWNER, create, stamp=Stamp())
        with self.assertRaisesRegex(Refused, "id_exists"):
            self.commands.execute(other, OWNER, replace(create, id="new-command"), stamp=Stamp())
        self.assertEqual(self.commands.read(other), State())
        with self.assertRaisesRegex(Refused, "not_found"):
            self.commands.execute(GROUP, OWNER, Command("short", 1, Reject("prefix")))

    def test_group_keys_include_scope_project_and_private_owner(self):
        groups = [Group("s", "project", "p"), Group("s", "project", "q"), Group("s", "group", "p"),
                  Group("s", "global"), Group("s", "user", owner="a"), Group("s", "user", owner="b"),
                  Group("s", "room", owner="a"), Group("s", "room", owner="b")]
        for i, group in enumerate(groups):
            self.commands.execute(group, OWNER, Command(f"c-{i}", 0, Correct(f"a-{i}", str(i))))
        for i, group in enumerate(groups):
            self.assertEqual(self.commands.read(group).head, f"a-{i}")
        for group in [Group("s", "room"), Group("s", "project"), Group("s", "global", "p"), Group("s", "project", "p", "a")]:
            with self.assertRaisesRegex(Refused, "invalid_group"):
                self.commands.read(group)

    def intercept_commit(self, hook):
        write = self.backend._write
        def intercepted(sqls):
            self.backend._write = write
            hook()
            return write(sqls)
        self.backend._write = intercepted
        self.addCleanup(setattr, self.backend, "_write", write)

    def intercept_receipt_miss(self, hook, after_claims=None):
        raw = self.backend.db
        fired = False
        class Cursor:
            def __init__(self): self.cursor = raw.cursor(); self.sql = ""
            def execute(self, sql, args=()):
                self.sql = sql; self.cursor.execute(sql, args); return self
            def fetchone(self):
                nonlocal fired
                row = self.cursor.fetchone()
                if not fired and row is None and self.sql.startswith("SELECT group_key,fingerprint,receipt"):
                    fired = True
                    hook()
                return row
            def fetchall(self):
                rows = self.cursor.fetchall()
                if after_claims is not None and self.sql.startswith("SELECT claim FROM"):
                    after_claims()
                return rows
            def __getattr__(self, name): return getattr(self.cursor, name)
        class Connection:
            def cursor(self): return Cursor()
            def __getattr__(self, name): return getattr(raw, name)
        self.backend.db = Connection()
        self.addCleanup(setattr, self.backend, "db", raw)

    def assert_command_rows(self, count):
        for table in ["claim_command_events", "claim_command_receipts"]:
            self.assertEqual(self.backend.db.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)
        self.assertEqual(self.backend.db.cursor().execute("SELECT COUNT(*) FROM claim_command_ids").fetchone()[0], 1)
        self.assertEqual(self.backend.db.cursor().execute("SELECT revision FROM claim_command_groups").fetchone()[0], count)

    def test_retry_after_receipt_miss_and_concurrent_commit_returns_original_receipt(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        self.intercept_receipt_miss(lambda: self.assertEqual(other.execute(GROUP, OWNER, command), SAME_RECEIPT))
        with patch.object(self.backend, "_write", side_effect=AssertionError("retry must not write")):
            self.assertEqual(self.commands.execute(GROUP, OWNER, command), SAME_RECEIPT)
        self.assert_command_rows(1)

    def exercise_load_retry_exhaustion(self, same_command):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        first = command if same_command else replace(command, id="unrelated")
        revisions = []
        def change_between_headers():
            self.assertLess(len(revisions), 5, "load exceeded its retry bound")
            revision = len(revisions) + 1
            receipt = other.execute(GROUP, OWNER, Command(f"churn-{revision}", revision,
                                                        Correct(f"churn-{revision}", "new answer")))
            revisions.append(receipt.revision)
        self.intercept_receipt_miss(lambda: other.execute(GROUP, OWNER, first), change_between_headers)
        with patch.object(self.backend, "_write") as write, patch("kypp.claim_commands.transition", wraps=transition) as plan:
            if same_command:
                self.assertEqual(self.commands.execute(GROUP, OWNER, command), SAME_RECEIPT)
            else:
                with self.assertRaisesRegex(Refused, "stale"):
                    self.commands.execute(GROUP, OWNER, command)
            write.assert_not_called()
            self.assertEqual(plan.call_count, 6)  # Only the other connection's six commits planned.
        self.assertEqual(revisions, [2, 3, 4, 5, 6])
        for table in ["claim_command_ids", "claim_command_events", "claim_command_receipts"]:
            self.assertEqual(self.backend.db.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 6)
        self.assertEqual(self.backend.db.cursor().execute("SELECT revision FROM claim_command_groups").fetchone()[0], 6)
        row = self.backend.db.cursor().execute("SELECT receipt FROM claim_command_receipts WHERE command_id='same'").fetchone()
        self.assertEqual(json.loads(row[0]) if row else None, asdict(SAME_RECEIPT) if same_command else None)

    def test_load_retry_exhaustion_recovers_concurrent_exact_receipt(self):
        self.exercise_load_retry_exhaustion(same_command=True)

    def test_load_retry_exhaustion_without_matching_receipt_stays_stale(self):
        self.exercise_load_retry_exhaustion(same_command=False)

    def test_retry_after_receipt_miss_and_concurrent_retirement_returns_original_receipt(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        def commit_and_retire():
            other.execute(GROUP, OWNER, command)
            other.execute(GROUP, OWNER, Command("retire", 1, Reject("a")))
        self.intercept_receipt_miss(commit_and_retire)
        with patch.object(self.backend, "_write", side_effect=AssertionError("retry must not write")):
            self.assertEqual(self.commands.execute(GROUP, OWNER, command), SAME_RECEIPT)
        self.assert_command_rows(2)
        self.assertEqual(self.commands.read(GROUP).get("a").status, "rejected")
        self.assertIsNone(self.commands.read(GROUP).head)

    def test_changed_payload_after_receipt_miss_reports_conflict_not_stale(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        self.intercept_receipt_miss(lambda: other.execute(GROUP, OWNER, command))
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            self.commands.execute(GROUP, OWNER, replace(command, action=Correct("a", "different")))
        self.assert_command_rows(1)
        self.assertEqual(self.commands.read(GROUP).get("a").content, "answer")

    def test_unrelated_commit_after_receipt_miss_still_refuses_stale_without_writes(self):
        other = LocalClaimCommands(self.open("main"))
        self.intercept_receipt_miss(lambda: other.execute(GROUP, OWNER, Command("first", 0, Correct("a", "answer"))))
        with self.assertRaisesRegex(Refused, "stale"):
            self.commands.execute(GROUP, OWNER, Command("late", 0, Correct("b", "stale")))
        self.assert_command_rows(1)
        self.assertIsNone(self.backend.db.cursor().execute("SELECT receipt FROM claim_command_receipts WHERE command_id='late'").fetchone())

    def test_changed_revision_after_receipt_miss_reports_conflict_not_id_exists(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        self.intercept_receipt_miss(lambda: other.execute(GROUP, OWNER, command))
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            self.commands.execute(GROUP, OWNER, replace(command, expected_revision=1))
        self.assert_command_rows(1)

    def test_read_storage_failure_is_not_masked_by_a_concurrent_receipt(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        self.intercept_receipt_miss(lambda: other.execute(GROUP, OWNER, command))
        def broken_read(*args, **kwargs):
            self.backend.db.cursor().execute("SELECT * FROM missing_table").fetchone()
        with patch.object(self.commands, "_read", side_effect=broken_read):
            with self.assertRaisesRegex(Exception, "missing_table"):
                self.commands.execute(GROUP, OWNER, command)
        self.assert_command_rows(1)

    def test_storage_refusal_is_not_masked_by_a_concurrent_receipt(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        def commit_and_corrupt_projection():
            other.execute(GROUP, OWNER, command)
            other.backend._write([("UPDATE claim_command_ids SET claim=NULL WHERE claim_id='a'", ())])
        self.intercept_receipt_miss(commit_and_corrupt_projection)
        with self.assertRaisesRegex(Refused, "storage_inconsistent"):
            self.commands.execute(GROUP, OWNER, command)
        self.assert_command_rows(1)

    def test_invalid_actor_and_policy_are_refused_before_receipt_lookup(self):
        command = Command("same", 0, Correct("a", "answer"))
        self.commands.execute(GROUP, OWNER, command)
        with patch.object(self.commands, "_replay", side_effect=AssertionError("must validate before replay")):
            for actor in [Actor("owner", "revoked"), Actor("", "operator")]:
                with self.assertRaisesRegex(Refused, "forbidden"):
                    self.commands.execute(GROUP, actor, command)
            with self.assertRaisesRegex(Refused, "invalid"):
                self.commands.execute(GROUP, OWNER, command, k=1)
        self.assert_command_rows(1)

    def test_role_change_cannot_replay_operator_receipt(self):
        command = Command("same", 0, Correct("a", "answer"))
        self.commands.execute(GROUP, OWNER, command)
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            self.commands.execute(GROUP, Actor("owner", "agent"), command)
        self.assert_command_rows(1)

    def test_caller_must_reauthorize_group_reach_before_replay(self):
        # Contract fixture for the future transport boundary, not production auth.
        allowed = {GROUP.key}
        command = Command("same", 0, Correct("a", "answer"))
        def authorized_execute(group):
            if group.key not in allowed:
                raise Refused("forbidden")
            return self.commands.execute(group, OWNER, command)
        self.assertEqual(authorized_execute(GROUP), SAME_RECEIPT)
        allowed.clear()
        with patch.object(self.commands, "_replay", side_effect=AssertionError("revoked reach must not read receipts")):
            with self.assertRaisesRegex(Refused, "forbidden"):
                authorized_execute(GROUP)
        # The in-process adapter intentionally trusts its caller's scope decision.
        self.assertEqual(self.commands.execute(GROUP, OWNER, command), SAME_RECEIPT)
        self.assert_command_rows(1)

    def test_two_connections_recheck_revision_at_the_commit_boundary(self):
        other = LocalClaimCommands(self.open("main"))
        late = Command("late", 0, Correct("late", "stale plan"))
        self.intercept_commit(lambda: other.execute(GROUP, OWNER, Command("first", 0, Correct("first", "new answer"))))
        with self.assertRaisesRegex(Refused, "stale"):
            self.commands.execute(GROUP, OWNER, late)
        state = self.commands.read(GROUP)
        self.assertEqual((state.head, state.revision, len(state.receipts)), ("first", 1, 1))
        self.assertEqual([c.id for c in state.claims], ["first"])

    def test_read_retries_when_another_connection_commits_between_queries(self):
        self.commands.execute(GROUP, OWNER, Command("first", 0, Correct("a", "first")))
        other = LocalClaimCommands(self.open("main"))
        raw = self.backend.db
        fired = False
        class Cursor:
            def __init__(self): self.cursor = raw.cursor(); self.sql = ""
            def execute(self, sql, args=()):
                self.sql = sql; self.cursor.execute(sql, args); return self
            def fetchall(self):
                nonlocal fired
                rows = self.cursor.fetchall()
                if not fired and self.sql.startswith("SELECT claim FROM"):
                    fired = True
                    other.execute(GROUP, OWNER, Command("second", 1, Correct("b", "second")))
                return rows
            def __getattr__(self, name): return getattr(self.cursor, name)
        class Connection:
            def cursor(self): return Cursor()
            def __getattr__(self, name): return getattr(raw, name)
        self.backend.db = Connection()
        try:
            state = self.commands.read(GROUP)
        finally:
            self.backend.db = raw
        self.assertTrue(fired)
        self.assertEqual(state, other.read(GROUP))
        self.assertEqual((state.head, state.revision), ("b", 2))

    def test_simultaneous_duplicate_returns_one_receipt_and_event(self):
        other = LocalClaimCommands(self.open("main"))
        command = Command("same", 0, Correct("a", "answer"))
        first = []
        self.intercept_commit(lambda: first.append(other.execute(GROUP, OWNER, command)))
        self.assertEqual(self.commands.execute(GROUP, OWNER, command), first[0])
        state = self.commands.read(GROUP)
        self.assertEqual((state.revision, len(state.events), len(state.receipts)), (1, 1, 1))

    def test_actual_concurrent_connections_have_exactly_one_cas_winner(self):
        barrier, bootstrap = Barrier(2), Lock()
        path = str(Path(self.tmp.name) / "main.db")
        def writer(i):
            # DDL bootstrap is not under test; each thread owns its DB connection.
            with bootstrap:
                backend = self.backend_type(path)
                commands = LocalClaimCommands(backend)
            write = backend._write
            def race(sqls):
                barrier.wait(timeout=5)
                return write(sqls)
            backend._write = race
            try:
                return commands.execute(GROUP, OWNER, Command(f"writer-{i}", 0, Correct(f"claim-{i}", "answer")))
            except Refused as error:
                return error.code
            finally:
                backend.db.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(writer, range(2)))
        self.assertEqual(sum(result == "stale" for result in results), 1, results)
        state = self.commands.read(GROUP)
        self.assertEqual((state.revision, len(state.claims), len(state.events), len(state.receipts)), (1, 1, 1, 1))

    def test_simultaneous_retargeted_command_rolls_back_every_write(self):
        other = LocalClaimCommands(self.open("main"))
        elsewhere = Group("other", "project", "p")
        command = Command("same", 0, Correct("a", "answer"))
        self.intercept_commit(lambda: other.execute(elsewhere, OWNER, replace(command, action=Correct("b", "elsewhere"))))
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            self.commands.execute(GROUP, OWNER, command)
        self.assertEqual(self.commands.read(GROUP), State())
        count = self.backend.db.cursor().execute("SELECT COUNT(*) FROM claim_command_ids WHERE claim_id='a'").fetchone()[0]
        self.assertEqual(count, 0)

    def test_failure_after_snapshot_write_rolls_back_history_and_id_index(self):
        write = self.backend._write
        def fail(sqls):
            return write(sqls[:-1] + [("INSERT INTO missing_table VALUES(1)", ())] + sqls[-1:])
        self.backend._write = fail
        try:
            with self.assertRaises(Exception):
                self.commands.execute(GROUP, OWNER, Command("create", 0, Correct("a", "answer")))
        finally:
            self.backend._write = write
        self.assertEqual(self.commands.read(GROUP), State())
        for table in ["claim_command_groups", "claim_command_ids", "claim_command_receipts"]:
            self.assertEqual(self.backend.db.cursor().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.commands.execute(GROUP, OWNER, Command("create", 0, Correct("a", "answer")))

    def test_event_and_receipt_failure_roll_back_existing_projection_and_head(self):
        self.commands.execute(GROUP, OWNER, Command("create", 0, Correct("a", "first")))
        before = self.commands.read(GROUP)
        write = self.backend._write
        for table in ["claim_command_events", "claim_command_receipts"]:
            def fail(sqls):
                changed = [("INSERT INTO missing_table VALUES(1)", ()) if sql.startswith(f"INSERT INTO {table}(") else (sql, args)
                           for sql, args in sqls]
                return write(changed)
            self.backend._write = fail
            try:
                with self.assertRaises(Exception):
                    self.commands.execute(GROUP, OWNER, Command("fix", 1, Correct("b", "replacement")))
            finally:
                self.backend._write = write
            self.assertEqual(self.commands.read(GROUP), before)
            self.assertEqual(self.backend.db.cursor().execute("SELECT COUNT(*) FROM claim_command_ids WHERE claim_id='b'").fetchone()[0], 0)
        self.commands.execute(GROUP, OWNER, Command("fix", 1, Correct("b", "replacement")))

    def test_thousands_of_checks_keep_rows_small_and_old_receipts_replayable(self):
        if isinstance(self.backend.db, sqlite3.Connection):
            self.backend.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2_000_000)
        create = Command("create", 0, Propose("a", "answer", verifier="true"))
        original = self.commands.execute(GROUP, OWNER, create, stamp=Stamp("original"))
        first_check = None
        revision = 1
        for i in range(2000):
            command = Command(f"begin-{i}", revision, BeginCheck("a"))
            token = self.commands.execute(GROUP, OWNER, command)
            if i == 0:
                first_check = (command, token)
            revision += 1
            self.commands.execute(GROUP, OWNER, Command(f"complete-{i}", revision, CompleteCheck(token.token, "passed")))
            revision += 1
        state = self.commands.read(GROUP)
        self.assertEqual((state.revision, len(state.events), len(state.receipts)), (4001, 4001, 4001))
        self.assertGreater(len(encoded(asdict(state)).encode()), 2_000_000)
        row = self.backend.db.cursor().execute("SELECT state FROM claim_command_groups WHERE group_key=?", (GROUP.key,)).fetchone()
        self.assertLess(len(row[0].encode()), 200)
        self.assertNotIn("receipts", json.loads(row[0]))
        self.assertNotIn("events", json.loads(row[0]))
        reopened = LocalClaimCommands(self.open("main"))
        self.assertEqual(reopened.execute(GROUP, OWNER, create, stamp=Stamp("different")), original)
        self.assertEqual(reopened.execute(GROUP, OWNER, first_check[0]), first_check[1])
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            reopened.execute(Group("elsewhere", "project", "p"), OWNER, create, stamp=Stamp())
        with self.assertRaisesRegex(Refused, "idempotency_conflict"):
            reopened.execute(GROUP, OWNER, replace(create, action=Propose("a", "changed")), stamp=Stamp())
        self.assertEqual(reopened.read(GROUP), state)

    def test_many_large_claims_do_not_become_one_oversized_row(self):
        if isinstance(self.backend.db, sqlite3.Connection):
            self.backend.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2_000_000)
        for i in range(500):
            self.commands.execute(GROUP, OWNER, Command(f"c-{i}", i, Propose(f"a-{i}", "x" * 4000)), stamp=Stamp(f"s-{i}"))
        state = self.commands.read(GROUP)
        self.assertEqual(len(state.claims), 500)
        self.assertGreater(sum(len(c.content.encode()) for c in state.claims), 1_999_999)
        rows = self.backend.db.cursor().execute("SELECT claim FROM claim_command_ids WHERE group_key=? ORDER BY ordinal", (GROUP.key,)).fetchall()
        self.assertLess(max(len(row[0].encode()) for row in rows), 5000)
        # Correction touches all 500 projections, in the same guarded transaction.
        self.commands.execute(GROUP, OWNER, Command("fix", 500, Correct("human", "right")))
        corrected = self.commands.read(GROUP)
        self.assertEqual(corrected.head, "human")
        self.assertEqual(len(corrected.claims), 501)
        self.assertTrue(all(c.status == "superseded" for c in corrected.claims[:-1]))

    def old_snapshot(self, name="scheduled check recovery"):
        case = next(case for case in vectors() if name in case["name"])
        state = case["steps"][-1]["state"]
        sqls = [(SAVE, (GROUP.key, state.revision, encoded(asdict(state))))]
        sqls += [("INSERT INTO claim_command_ids(claim_id,group_key) VALUES(?,?)", (c.id, GROUP.key)) for c in state.claims]
        sqls += [("INSERT INTO claim_command_receipts(principal,command_id,group_key,fingerprint,receipt) VALUES(?,?,?,?,?)",
                  (r.principal, r.command_id, GROUP.key, r.fingerprint, encoded(asdict(r)))) for r in state.receipts]
        self.backend._write(sqls)
        return case, state

    def test_previous_staged_snapshot_normalizes_without_losing_history_or_replay(self):
        case, state = self.old_snapshot()
        for _ in range(2):  # reopen is idempotent, not a second append of old events
            reopened = LocalClaimCommands(self.open("main"))
            self.assertEqual(reopened.read(GROUP), state)
            for step in case["steps"]:
                self.assertEqual(reopened.execute(GROUP, step["actor"], step["command"], stamp=step["stamp"]), step["receipt"])
        result = reopened.execute(GROUP, OWNER, Command("new-check", state.revision, BeginCheck("a")))
        self.assertEqual(result.token.attempt, 4)

    def test_snapshot_conversion_preserves_recurrence_evidence_and_retired_sources(self):
        _, state = self.old_snapshot("new recurrence observation")
        reopened = LocalClaimCommands(self.open("main"))
        self.assertEqual(reopened.read(GROUP), state)
        self.assertTrue(state.events[-1].evidence)
        self.assertTrue(any(c.status == "superseded" and c.source for c in state.claims))

    def test_snapshot_conversion_failure_preserves_old_data_for_retry(self):
        _, state = self.old_snapshot()
        write = self.backend._write
        def fail(sqls):
            return write([sqls[0], ("INSERT INTO missing_table VALUES(1)", ()), *sqls[1:]])
        self.backend._write = fail
        try:
            with self.assertRaises(Exception):
                LocalClaimCommands(self.backend)
        finally:
            self.backend._write = write
        raw = self.backend.db.cursor().execute("SELECT state FROM claim_command_groups WHERE group_key=?", (GROUP.key,)).fetchone()[0]
        self.assertEqual(raw, encoded(asdict(state)))
        self.assertEqual(self.backend.db.cursor().execute("SELECT COUNT(*) FROM claim_command_events").fetchone()[0], 0)
        self.assertEqual(LocalClaimCommands(self.backend).read(GROUP), state)

    def test_six_completion_correction_interleavings_match_kernel(self):
        for n, order in enumerate(permutations(["a", "b", "fix"])):
            commands = LocalClaimCommands(self.open(f"order-{n}"))
            state, tokens = State(), {}
            actions = [Propose("a", "old", verifier="check a"), BeginCheck("a"),
                       Propose("b", "other", verifier="check b"), BeginCheck("b")]
            for i, action in enumerate(actions):
                command = Command(str(i), state.revision, action)
                state, receipt = transition(state, OWNER, command, stamp=Stamp())
                self.assertEqual(commands.execute(GROUP, OWNER, command, stamp=Stamp()), receipt)
                if receipt.token:
                    tokens[action.claim_id] = receipt.token
            for i, item in enumerate(order):
                action = Correct("human", "right") if item == "fix" else CompleteCheck(tokens[item], "passed")
                command = Command(f"finish-{i}", state.revision, action)
                try:
                    state, receipt = transition(state, OWNER, command)
                except Refused as error:
                    with self.assertRaisesRegex(Refused, error.code):
                        commands.execute(GROUP, OWNER, command)
                else:
                    self.assertEqual(commands.execute(GROUP, OWNER, command), receipt)
                self.assertEqual(commands.read(GROUP), state)
            self.assertEqual(state.head, "human")

    def test_legacy_and_command_tables_are_not_a_partially_activated_store(self):
        old = self.backend.claim("fact", "port", "legacy", "project", project="p", accept=True)
        self.commands.execute(GROUP, OWNER, Command("new", 0, Correct("v1", "new protocol")))
        self.assertEqual(self.commands.read(GROUP).head, "v1")
        self.assertEqual([c.id for c in self.backend.recall("", project="p")], [old])
        self.backend.set_status(old, "rejected")
        self.assertEqual(self.commands.read(GROUP).revision, 1)


class SqliteCommands(CommandContract, unittest.TestCase):
    backend_type = SqliteFtsStore


class LibsqlCommands(CommandContract, unittest.TestCase):
    backend_type = LibsqlStore


class TursoCommands(CommandContract, unittest.TestCase):
    backend_type = MemoryStore


class SchemaParity(unittest.TestCase):
    def test_python_and_worker_command_tables_are_identical(self):
        migration = (Path(__file__).resolve().parents[1] / "worker/migrations/0010_claim_commands.sql").read_text()
        def normalized(sql):
            return re.sub(r"\s+", "", "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")))
        self.assertEqual(normalized(SCHEMA), normalized(migration))
        upgrade = (Path(__file__).resolve().parents[1] / "worker/migrations/0011_normalized_claim_commands.sql").read_text()
        self.assertEqual(normalized(NORMALIZE_DDL + NORMALIZE_DATA), normalized(upgrade))
