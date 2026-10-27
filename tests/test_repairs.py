"""Repair pitfalls: a failing shell form followed by one that works, mined without a model."""
import unittest

from kypp.distill import ClaimDraft, HeuristicDistiller, WithRepairs, build_trace
from kypp.repairs import mine


def _bash(command, output="", status="completed"):
    return {"sessionId": "s1", "payload": {"type": "tool_call", "name": "Bash", "status": status,
                                           "input": {"command": command}, "output": output}}


def _codex(command, output):
    return {"sessionId": "s1", "payload": {"type": "tool_call", "name": "shell", "status": "unspecified",
                                           "input": {"command": ["bash", "-lc", command]}, "output": output}}


def _mine(*events):
    return mine(build_trace(list(events)))[0]


class CommandMissing(unittest.TestCase):
    def test_wrapper_that_works_becomes_one_pitfall(self):
        [d] = _mine(_bash("pytest -q tests", "bash: line 1: pytest: command not found", "error"),
                    _bash("uv run pytest -q tests", "3 passed"))
        self.assertEqual((d.type, d.subject), ("pitfall", "`pytest` is not on PATH"))
        self.assertIn("`pytest -q …` fails here (command not found)", d.content)
        self.assertIn("`uv run pytest …` works", d.content)

    def test_subject_names_only_what_failed_so_sessions_agree(self):
        a = _mine(_bash("pytest", "pytest: command not found", "error"), _bash("python -m pytest", "ok"))
        b = _mine(_bash("pytest -x", "pytest: command not found", "error"), _bash(".venv/bin/pytest -x", "ok"))
        self.assertEqual([d.subject for d in a], [d.subject for d in b])
        self.assertIn("`.venv/bin/pytest …` works", b[0].content)

    def test_codex_reads_failure_from_the_exit_code(self):
        [d] = _mine(_codex("cd web && tsc --noEmit", "Exit code: 127\nOutput:\nbash: tsc: command not found"),
                    _codex("cd web && npx tsc --noEmit", "Exit code: 0\nOutput:\n"))
        self.assertEqual(d.subject, "`tsc` is not on PATH")
        self.assertIn("`npx tsc …` works", d.content)

    def test_codex_success_with_a_nonzero_exit_is_not_a_repair(self):
        self.assertEqual(_mine(_codex("tsc", "Exit code: 127\nbash: tsc: command not found"),
                               _codex("npx tsc", "Exit code: 2\nerror TS2304")), [])

    def test_no_fact_when_the_bare_form_works_later(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("uv run pytest", "ok"), _bash("pytest", "ok")), [])

    def test_no_fact_when_it_was_installed_in_between(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("uv pip install pytest", "ok"), _bash("python -m pytest", "ok")), [])

    def test_probes_and_installs_are_not_repairs(self):
        for later in ("which pytest", "pip install pytest", "uv add pytest", "pytest --version"):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok")),
                             [], later)

    def test_a_host_path_form_is_not_a_fact(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("/home/u/.venv/bin/pytest", "ok")), [])


    def test_a_piped_run_fails_by_its_output(self):
        [d] = _mine(_bash("pytest -q | tail -3", "bash: pytest: command not found"),
                    _bash("env -u CI uv run pytest -q | tail -3", "3 passed"))
        self.assertIn("`uv run pytest …` works", d.content)

    def test_a_multiline_command_is_read_line_by_line(self):
        [d] = _mine(_bash("cd web\ntsc", "tsc: command not found", "error"), _bash("cd web\nnpx tsc", "ok"))
        self.assertEqual(d.subject, "`tsc` is not on PATH")


class ModuleMissing(unittest.TestCase):
    def test_interpreter_that_can_import_it(self):
        [d] = _mine(_bash("python -m kypp.distill", "ModuleNotFoundError: No module named 'turso'", "error"),
                    _bash("uv run python -m kypp.distill", "OK"))
        self.assertEqual(d.subject, "`python` can't import `turso`")
        self.assertIn("(no module `turso`)", d.content)
        self.assertIn("`uv run python …` works", d.content)


class EnvMissing(unittest.TestCase):
    def test_setting_the_variable_works(self):
        [d] = _mine(_bash("node build.js", "Error: API_BASE is not set", "error"),
                    _bash("API_BASE=http://localhost:8787 node build.js", "built"))
        self.assertEqual(d.subject, "`node` needs `API_BASE` set")
        self.assertIn("asking for `API_BASE`; `API_BASE=http://localhost:8787 node …` works", d.content)

    def test_a_credential_value_is_never_repeated(self):
        [d] = _mine(_bash("python run.py", "KeyError: 'OPENAI_API_KEY'", "error"),
                    _bash("OPENAI_API_KEY=sk-live-123 python run.py", "ok"))
        self.assertNotIn("sk-live-123", d.content)
        self.assertIn("`OPENAI_API_KEY=… python …`", d.content)


class HeuristicFloor(unittest.TestCase):
    def test_explained_failures_drop_the_generic_pitfall(self):
        trace = build_trace([_bash("pytest", "pytest: command not found", "error"),
                             _bash("pytest -x", "pytest: command not found", "error"),
                             _bash("uv run pytest -x", "ok")])
        self.assertEqual([d.subject for d in HeuristicDistiller().distill(trace)], ["`pytest` is not on PATH"])

    def test_unexplained_failures_keep_it(self):
        trace = build_trace([_bash("cargo build", "error[E0425]", "error"),
                             _bash("cargo build", "error[E0425]", "error")])
        self.assertEqual([d.subject for d in HeuristicDistiller().distill(trace)], ["Bash repeatedly failed"])

    def test_llm_path_gets_repairs_without_duplicates(self):
        class Inner:
            def distill(self, _t):
                return [ClaimDraft("pitfall", "`pytest` is not on PATH", "model's wording", 0.7)]
        trace = build_trace([_bash("pytest", "pytest: command not found", "error"), _bash("uv run pytest", "ok")])
        self.assertEqual([d.content for d in WithRepairs(Inner()).distill(trace)], ["model's wording"])
        class Empty:
            def distill(self, _t):
                return []
        self.assertEqual(len(WithRepairs(Empty()).distill(trace)), 1)


if __name__ == "__main__":
    unittest.main()
