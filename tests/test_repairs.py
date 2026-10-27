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
        self.assertIn("`pytest …` fails here (command not found)", d.content)
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

    def test_codex_exec_command_exit_format(self):
        [d] = _mine(_codex("pytest", "Process exited with code 127\nbash: pytest: command not found"),
                    _codex("uv run pytest", "Process exited with code 0\n3 passed"))
        self.assertEqual(d.subject, "`pytest` is not on PATH")

    def test_codex_success_with_a_nonzero_exit_is_not_a_repair(self):
        self.assertEqual(_mine(_codex("tsc", "Exit code: 127\nbash: tsc: command not found"),
                               _codex("npx tsc", "Exit code: 2\nerror TS2304")), [])

    def test_no_fact_when_the_bare_form_works_later(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("uv run pytest", "ok"), _bash("pytest", "ok")), [])

    def test_no_fact_when_it_was_installed_in_between(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("uv pip install pytest", "ok"), _bash("python -m pytest", "ok")), [])

    def test_no_fact_when_the_working_run_installed_it_first(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("uv pip install pytest && uv run pytest", "ok")), [])

    def test_probes_and_installs_are_not_repairs(self):
        for later in ("which pytest", "pip install pytest", "uv add pytest", "pytest --version",
                      "test -x .venv/bin/pytest", "test .venv/bin/pytest", "ls .venv/bin/pytest",
                      "touch pytest", "true pytest"):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok")),
                             [], later)

    def test_a_host_path_form_is_not_a_fact(self):
        self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                               _bash("/home/u/.venv/bin/pytest", "ok")), [])


    def test_a_piped_run_fails_by_its_output(self):
        [d] = _mine(_bash("pytest -q | tail -3", "bash: pytest: command not found"),
                    _bash("uv run pytest -q", "3 passed"))
        self.assertIn("`uv run pytest …` works", d.content)

    def test_a_successful_run_that_prints_error_text_is_not_a_failure(self):
        self.assertEqual(_mine(_bash("rg \"No module named 'turso'\" errors.log", "No module named 'turso'"),
                               _bash("uv run rg \"No module named 'turso'\" errors.log", "No module named 'turso'")), [])

    def test_a_number_before_a_spaced_redirection_is_an_argument(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv run python app.py 2 >out", "ok")), [])
        self.assertEqual(_mine(_bash("python app.py 2 > /dev/null", "No module named 'turso'", "error"),
                               _bash("uv run python app.py", "ok")), [])

    def test_redirections_are_not_arguments(self):
        [d] = _mine(_bash("python app.py", "No module named 'turso'", "error"),
                    _bash("uv run python app.py >out 2>&1", "ok"))
        self.assertIn("`uv run python …` works", d.content)

    def test_flags_on_the_working_run_are_not_installs(self):
        [d] = _mine(_bash("pytest", "pytest: command not found", "error"), _bash("uv run pytest -v", "ok"))
        self.assertIn("`uv run pytest …` works", d.content)

    def test_success_proves_only_segments_it_ran(self):
        for later in ("true || uv run pytest", "uv run pytest; true", "uv run pytest | tail -3",
                      "uv run pytest |tail -3", "uv run pytest |& tail -3", "printf '' uv run pytest",
                      "echo $(true; uv run pytest 2>/dev/null)", "uv run pytest 2>/dev/null; > out.log", "echo `uv run pytest`", "uv run pytest >/tmp/log 2>&1 &",
                      "echo 'example; uv run pytest && end'", "env -u PYTHONPATH uv run pytest",
                      "echo ';' uv run pytest", 'echo "a|b" uv run pytest', "echo \\; uv run pytest"):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok")),
                             [], later)
        [d] = _mine(_bash("pytest", "pytest: command not found", "error"), _bash("cd x; uv run pytest && echo ok", "ok"))
        self.assertIn("`uv run pytest …` works", d.content)

    def test_comments_and_continuations_read_as_the_shell_does(self):
        for later in ("# run tests\nuv run pytest", "uv run pytest;", "uv run pytest\n# done", "cd x  # into the repo\nuv run pytest", "uv run \\\n  pytest"):
            [d] = _mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok"))
            self.assertIn("`uv run pytest …` works", d.content, later)

    def test_a_failure_under_its_own_env_change_says_nothing(self):
        for failed in ("PATH=/nonexistent pytest", "export PATH=/x; pytest"):
            self.assertEqual(_mine(_bash(failed, "pytest: command not found", "error"),
                                   _bash("uv run pytest", "ok")), [], failed)

    def test_a_multiline_command_is_read_line_by_line(self):
        [d] = _mine(_bash("cd web\ntsc", "tsc: command not found", "error"), _bash("cd web\nnpx tsc", "ok"))
        self.assertEqual(d.subject, "`tsc` is not on PATH")


    def test_probe_flags_after_the_command_are_not_repairs(self):
        for later in ("uv run pytest --version", ".venv/bin/pytest --help", "uv run pytest --help tests",
                      "python inspect_args.py pytest"):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok")),
                             [], later)

    def test_a_run_with_no_recorded_outcome_proves_nothing(self):
        for status, out in (("running", ""), ("unspecified", ""), ("unspecified", "3 passed")):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"),
                                   _bash("uv run pytest", out, status)), [], status)

    def test_credentials_in_the_working_form_are_redacted(self):
        for later in ("docker run -e OPENAI_API_KEY=sk-live-123 img pytest -q",
                      "docker --config x run --token SECRET image pytest"):
            self.assertEqual(_mine(_bash("pytest", "pytest: command not found", "error"), _bash(later, "ok")),
                             [], later)
        [d] = _mine(_bash("mycli --token=sk-1 sync", "mycli: command not found", "error"), _bash("npx mycli --token=sk-1", "ok"))
        self.assertNotIn("sk-1", d.content)


class ModuleMissing(unittest.TestCase):
    def test_interpreter_that_can_import_it(self):
        [d] = _mine(_bash("python -m kypp.distill", "ModuleNotFoundError: No module named 'turso'", "error"),
                    _bash("uv run python -m kypp.distill", "OK"))
        self.assertEqual(d.subject, "`python` can't import `turso`")
        self.assertIn("(no module `turso`)", d.content)
        self.assertIn("`uv run python …` works", d.content)


    def test_the_same_script_name_elsewhere_is_not_the_same_script(self):
        self.assertEqual(_mine(_bash("cd server && python app.py", "No module named 'project'", "error"),
                               _bash("cd client && uv run python app.py", "ok")), [])
        [d] = _mine(_bash("cd server && python app.py", "No module named 'project'", "error"),
                    _bash("cd server && uv run python app.py", "ok"), _bash("cd client && python app.py", "ok"))
        self.assertIn("`uv run python …` works", d.content)

    def test_a_script_help_probe_is_not_a_fix(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv run python app.py --help", "ok")), [])

    def test_extra_arguments_are_not_the_same_run(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'psycopg'", "error"),
                               _bash("uv run python app.py --skip-database", "ok")), [])
        self.assertEqual(_mine(_bash("node build.js", "Error: API_BASE is not set", "error"),
                               _bash("API_BASE=x node build.js --dry-run", "ok")), [])

    def test_the_failing_form_shows_only_the_script(self):
        [d] = _mine(_bash("python app.py --token SECRET", "No module named 'turso'", "error"),
                    _bash("uv run python app.py --token SECRET", "ok"))
        self.assertNotIn("SECRET", d.content)
        self.assertIn("`python app.py …` fails here", d.content)

    def test_a_cd_after_the_command_leaves_the_place_unclear(self):
        self.assertEqual(_mine(_bash("python app.py; cd server", "No module named 'turso'", "error"),
                               _bash("cd server; uv run python app.py", "ok")), [])

    def test_an_install_joined_by_a_separator_still_counts(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv sync; uv run python app.py", "ok")), [])

    def test_a_failure_without_its_own_repair_is_not_explained(self):
        drafts, explained = mine(build_trace([_bash("python app.py", "No module named 'project'", "error"),
                                              _bash("uv run python app.py", "ok"),
                                              _bash("python other.py", "No module named 'project'", "error")]))
        self.assertEqual((len(drafts), explained), (1, {0}))

    def test_an_edit_in_between_may_have_changed_the_script(self):
        edit = {"sessionId": "s1", "payload": {"type": "tool_call", "name": "Edit", "status": "completed",
                                               "input": {"file_path": "app.py"}, "output": "ok"}}
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"), edit,
                               _bash("uv run python app.py", "ok")), [])

    def test_a_shell_edit_in_between_may_have_changed_the_script(self):
        for edit in ("sed -i '/turso/d' app.py", "python - <<'EOF'\nPath('app.py').write_text('')\nEOF",
                     "echo 'print(1)' > app.py"):
            self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"), _bash(edit, ""),
                                   _bash("uv run python app.py", "ok")), [], edit)

    def test_an_error_from_a_pipeline_is_not_attributed(self):
        self.assertEqual(_mine(_bash("python producer.py | python consumer.py", "No module named 'turso'"),
                               _bash("uv run python producer.py", "ok")), [])

    def test_an_edit_in_the_working_run_itself_counts(self):
        for later in ("sed -i '/import turso/d' app.py && uv run python app.py",
                      "echo 'print(1)' > app.py && uv run python app.py", "echo x | tee app.py; uv run python app.py"):
            self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"), _bash(later, "ok")), [], later)

    def test_a_quoted_angle_bracket_is_an_argument(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv run python app.py '>' --help", "usage")), [])

    def test_a_path_form_from_another_directory_is_not_a_fix(self):
        self.assertEqual(_mine(_bash("pytest -q", "pytest: command not found", "error"),
                               _bash("cd backend && .venv/bin/pytest -q", "ok")), [])

    def test_a_url_or_user_in_the_working_form_is_never_written(self):
        self.assertEqual(_mine(_bash("mycli", "mycli: command not found", "error"),
                               _bash("npx https://user:TOKEN@packages.example/mycli", "ok")), [])

    def test_a_redirection_only_command_is_skipped(self):
        [d] = _mine(_bash("> out.log", ""), _bash("python app.py", "No module named 'turso'", "error"),
                    _bash("uv run python app.py 2>/dev/null", "ok"))
        self.assertIn("`uv run python …` works", d.content)

    def test_unset_is_an_env_change(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("unset PYTHONHOME; uv run python app.py", "ok")), [])

    def test_a_different_subcommand_is_not_the_same_run(self):
        fail = _bash("python manage.py migrate", "No module named 'psycopg'", "error")
        self.assertEqual(_mine(fail, _bash("uv run python manage.py check", "ok")), [])
        [d] = _mine(fail, _bash("uv run python manage.py migrate", "ok"), _bash("python manage.py check", "ok"))
        self.assertIn("`uv run python …` works", d.content)

    def test_the_later_run_must_run_the_same_thing(self):
        for later in ("uv run python -c 'print(1)'", "uv run python unrelated.py", "uv run python -m other"):
            self.assertEqual(_mine(_bash("python -m kypp.distill", "No module named 'turso'", "error"),
                                   _bash(later, "ok")), [], later)

    def test_an_error_from_a_multi_command_run_is_not_attributed(self):
        self.assertEqual(_mine(_bash("python app.py; node build.js", "No module named 'turso'", "error"),
                               _bash("npx node build.js", "ok")), [])

    def test_a_fix_that_also_sets_a_variable_is_not_recorded(self):
        for later in ("PYTHONPATH=src uv run python app.py", "export PYTHONPATH=src && uv run python app.py",
                      "env -C other uv run python app.py"):
            self.assertEqual(_mine(_bash("python app.py", "No module named 'project'", "error"),
                                   _bash(later, "ok")), [], later)

    def test_a_sync_or_bulk_install_in_between_explains_it(self):
        for between in ("uv sync", "pip install -r requirements.txt"):
            self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"), _bash(between, "ok"),
                                   _bash("uv run python app.py", "ok")), [], between)

    def test_no_fact_when_the_working_run_installed_it_first(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv pip install turso && uv run python app.py", "ok")), [])

    def test_inline_code_is_not_compared_so_yields_nothing(self):
        self.assertEqual(_mine(_bash("python -c 'import turso'", "No module named 'turso'", "error"),
                               _bash("uv run python -c 'print(1)'", "ok")), [])

    def test_no_fact_when_the_bare_form_works_later(self):
        self.assertEqual(_mine(_bash("python app.py", "No module named 'turso'", "error"),
                               _bash("uv run python app.py", "ok"), _bash("python app.py", "ok")), [])

    def test_a_later_help_probe_does_not_count_as_working_bare(self):
        [d] = _mine(_bash("python app.py", "No module named 'turso'", "error"),
                    _bash("uv run python app.py", "ok"), _bash("python app.py --help", "usage: app"))
        self.assertIn("`uv run python …` works", d.content)


class EnvMissing(unittest.TestCase):
    def test_a_later_help_probe_does_not_count_as_working_without_it(self):
        [d] = _mine(_bash("node build.js", "Error: API_BASE is not set", "error"),
                    _bash("API_BASE=http://localhost:8787 node build.js", "built"), _bash("node build.js -h", "usage"))
        self.assertEqual(d.subject, "`node` needs `API_BASE` set")

    def test_setting_the_variable_works(self):
        [d] = _mine(_bash("node build.js", "Error: API_BASE is not set", "error"),
                    _bash("API_BASE=http://localhost:8787 node build.js", "built"))
        self.assertEqual(d.subject, "`node` needs `API_BASE` set")
        self.assertIn("asking for `API_BASE`; `API_BASE=… node …` works", d.content)

    def test_only_known_safe_values_are_written(self):
        [d] = _mine(_bash("python app.py", "DATABASE_URL is required", "error"),
                    _bash("DATABASE_URL=postgresql://u:pw@h/db python app.py", "ok"))
        self.assertNotIn("pw", d.content)
        [d] = _mine(_bash("python -m app", "KeyError: 'PYTHONPATH'", "error"), _bash("PYTHONPATH=src python -m app", "ok"))
        self.assertIn("`PYTHONPATH=src python …` works", d.content)
        [d] = _mine(_bash("python app.py", "HOST is required", "error"),
                    _bash("HOST=https://u:pw@example.com python app.py", "ok"))
        self.assertNotIn("pw", d.content)
        [d] = _mine(_bash("python app.py", "HOST is required", "error"),
                    _bash("HOST='https://api.example/?token=SECRET' python app.py", "ok"))
        self.assertNotIn("SECRET", d.content)

    def test_the_later_run_must_run_the_same_script(self):
        self.assertEqual(_mine(_bash("node build.js", "API_BASE is not set", "error"),
                               _bash("API_BASE=x node --version", "ok")), [])

    def test_a_probe_with_the_variable_set_is_not_a_fix(self):
        self.assertEqual(_mine(_bash("node build.js", "API_BASE is not set", "error"),
                               _bash("API_BASE=x node build.js --help", "ok")), [])

    def test_no_fact_when_something_was_installed_in_between(self):
        self.assertEqual(_mine(_bash("node build.js", "Error: API_BASE is not set", "error"), _bash("npm install", "ok"),
                               _bash("API_BASE=http://localhost:8787 node build.js", "built")), [])

    def test_a_fix_with_other_setup_in_the_run_is_not_recorded(self):
        for later in ("export PYTHONPATH=src && API_BASE=x python app.py", "source .env && API_BASE=x python app.py"):
            self.assertEqual(_mine(_bash("python app.py", "Error: API_BASE is not set", "error"),
                                   _bash(later, "ok")), [], later)

    def test_a_later_bare_run_after_an_export_is_not_unchanged(self):
        [d] = _mine(_bash("node build.js", "Error: API_BASE is not set", "error"),
                    _bash("API_BASE=http://localhost:8787 node build.js", "built"),
                    _bash("export API_BASE=x && node build.js", "built"))
        self.assertEqual(d.subject, "`node` needs `API_BASE` set")

    def test_a_fix_with_other_variables_too_is_not_recorded(self):
        self.assertEqual(_mine(_bash("python app.py", "API_BASE is not set", "error"),
                               _bash("API_BASE=x PYTHONPATH=src python app.py", "ok")), [])

    def test_a_later_run_with_its_own_env_change_is_not_unchanged(self):
        [d] = _mine(_bash("python app.py", "No module named 'project'", "error"),
                    _bash("uv run python app.py", "ok"), _bash("PYTHONPATH=src python app.py", "ok"))
        self.assertIn("`uv run python …` works", d.content)

    def test_no_fact_when_it_works_later_without_the_variable(self):
        self.assertEqual(_mine(_bash("node build.js", "API_BASE is not set", "error"),
                               _bash("API_BASE=x node build.js", "ok"), _bash("node build.js", "ok")), [])

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
