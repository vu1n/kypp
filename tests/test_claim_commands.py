"""Real local SQL transactions, not only pure-state tests. No live services."""
from dataclasses import replace
from itertools import permutations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import re
from threading import Barrier, Lock
import unittest

from kypp.backends import LibsqlStore, SqliteFtsStore
from kypp.claim_commands import Group, LocalClaimCommands, SCHEMA
from kypp.claim_machine import (
    Actor, BeginCheck, Command, CompleteCheck, Correct, Propose, Refused, Reject, Stamp, State, transition,
)
from kypp.store import MemoryStore
from claim_trace_oracle import vectors

OWNER = Actor("owner", "operator")
GROUP = Group("port", "project", "p")


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

    def test_two_connections_recheck_revision_at_the_commit_boundary(self):
        other = LocalClaimCommands(self.open("main"))
        late = Command("late", 0, Correct("late", "stale plan"))
        self.intercept_commit(lambda: other.execute(GROUP, OWNER, Command("first", 0, Correct("first", "new answer"))))
        with self.assertRaisesRegex(Refused, "stale"):
            self.commands.execute(GROUP, OWNER, late)
        state = self.commands.read(GROUP)
        self.assertEqual((state.head, state.revision, len(state.receipts)), ("first", 1, 1))
        self.assertEqual([c.id for c in state.claims], ["first"])

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
