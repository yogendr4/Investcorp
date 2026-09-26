"""Unit tests for src/baseline/claude_cli.py. The real Claude CLI is never invoked:
process execution and executable lookup are mocked.

Run from the project root:  python -m unittest tests.baseline.test_claude_cli -v
"""
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from src.baseline import claude_cli
from src.baseline.claude_cli import ClaudeCliAdapter, ClaudeCliConfig, ClaudeCliError, ErrorClass

Proc = claude_cli._ProcOutput
EMAIL = "person@example.com"
ORG_ID = "11111111-2222-3333-4444-555555555555"
VERSION = "2.1.283 (Claude Code)"

VERSION_OK = Proc(VERSION + "\n", "", 0, False)
AUTH_OK = Proc(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": EMAIL, "orgId": ORG_ID}), "", 0, False)


def good_json(**over):
    d = {"type": "result", "subtype": "success", "is_error": False, "result": "OK", "duration_ms": 1167,
         "duration_api_ms": 1969, "ttft_ms": 1061, "modelUsage": {"claude-sonnet-5": {}, "claude-haiku-4-5-20251001": {}}}
    d.update(over)
    return json.dumps(d)


class FakeRunner:
    """Stands in for _run_process; answers by command shape and records every call."""

    def __init__(self, version=VERSION_OK, auth=AUTH_OK, infer=None):
        self.version, self.auth, self.infer = version, auth, infer or Proc(good_json(), "", 0, False)
        self.calls = []

    def __call__(self, args, stdin_text, timeout_s, cwd):
        self.calls.append({"args": list(args), "stdin": stdin_text, "timeout": timeout_s, "cwd": cwd})
        if args[1:] == ["-v"]:
            r = self.version
        elif args[1:3] == ["auth", "status"]:
            r = self.auth
        else:
            r = self.infer
        if isinstance(r, Exception):
            raise r
        return r

    def inference_calls(self):
        return [c for c in self.calls if c["args"][1:2] == ["-p"]]


class AdapterTestCase(unittest.TestCase):
    which_result = "C:/fake/claude"

    def setUp(self):
        which = mock.patch.object(claude_cli.shutil, "which", return_value=self.which_result)
        which.start()
        self.addCleanup(which.stop)

    def adapter(self, runner, **cfg):
        patcher = mock.patch.object(claude_cli, "_run_process", side_effect=runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return ClaudeCliAdapter(ClaudeCliConfig(**cfg))


class TestSuccess(AdapterTestCase):
    def test_successful_json_response(self):
        runner = FakeRunner()
        r = self.adapter(runner).run("Return exactly OK.")
        self.assertTrue(r.success)
        self.assertEqual(r.result_text, "OK")
        self.assertEqual(r.exit_code, 0)
        self.assertFalse(r.timed_out)
        self.assertEqual(r.cli_version, VERSION)
        self.assertEqual(r.model, "claude-sonnet-5")
        self.assertIn("claude-haiku-4-5-20251001", r.models_used)
        self.assertEqual(r.stderr, "")
        self.assertIsNone(r.error_class)
        self.assertEqual(r.timing["duration_ms"], 1167)
        self.assertEqual(r.timing["duration_api_ms"], 1969)
        self.assertIn("wall_clock_ms", r.timing)

    def test_uses_approved_flags_and_stdin_prompt(self):
        runner = FakeRunner()
        prompt = "Sanjay L\u00f3pez \u00b1128 bps"
        self.adapter(runner).run(prompt)
        (call,) = runner.inference_calls()
        self.assertEqual(call["args"][1:], ["-p", "--output-format", "json", "--model", "claude-sonnet-5", "--effort", "low",
                                            "--tools", "", "--permission-prompts", "none", "--no-session-persistence", "--safe-mode"])
        self.assertEqual(call["stdin"], prompt)
        self.assertNotIn(prompt, call["args"])

    def test_default_effort_is_low_and_passed_to_cli(self):
        self.assertEqual(ClaudeCliConfig().effort, "low")
        self.assertEqual(claude_cli.DEFAULT_EFFORT, "low")
        runner = FakeRunner()
        r = self.adapter(runner).run("hi")
        args = runner.inference_calls()[0]["args"]
        self.assertIn("--effort", args)
        self.assertEqual(args[args.index("--effort") + 1], "low")
        self.assertEqual(r.argv[r.argv.index("--effort") + 1], "low")

    def test_custom_effort_is_passed_through(self):
        for effort in ("medium", "high", "max"):
            with self.subTest(effort=effort):
                runner = FakeRunner()
                self.adapter(runner, effort=effort).run("hi")
                args = runner.inference_calls()[0]["args"]
                self.assertEqual(args[args.index("--effort") + 1], effort)
                self.assertNotIn("low", args)

    def test_model_and_effort_are_independent_config(self):
        runner = FakeRunner()
        self.adapter(runner, model="claude-opus-x", effort="high").run("hi")
        args = runner.inference_calls()[0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-x")
        self.assertEqual(args[args.index("--effort") + 1], "high")

    def test_defaults(self):
        c = ClaudeCliConfig()
        self.assertEqual((c.executable, c.model, c.effort, c.timeout_s), ("claude", "claude-sonnet-5", "low", 60.0))
        self.assertEqual(c.working_dir, claude_cli.PROJECT_ROOT)
        self.assertTrue((claude_cli.PROJECT_ROOT / "CLAUDE.md").exists())
        runner = FakeRunner()
        self.adapter(runner).run("hi")
        self.assertTrue(all(c["timeout"] == 60.0 and c["cwd"] == claude_cli.PROJECT_ROOT for c in runner.calls))

    def test_configuration_is_applied(self):
        runner = FakeRunner()
        self.adapter(runner, executable="myclaude", model="sonnet", timeout_s=5, working_dir=Path("somewhere")).run("hi")
        call = runner.inference_calls()[0] if runner.inference_calls() else None
        self.assertIsNotNone(call)
        self.assertIn("sonnet", call["args"])
        self.assertEqual(call["timeout"], 5)
        self.assertEqual(call["cwd"], Path("somewhere"))

    def test_prerequisites_checked_once_per_adapter(self):
        runner = FakeRunner()
        a = self.adapter(runner)
        a.run("one")
        a.run("two")
        self.assertEqual(len([c for c in runner.calls if c["args"][1:] == ["-v"]]), 1)
        self.assertEqual(len(runner.inference_calls()), 2)

    def test_empty_prompt_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.adapter(FakeRunner()).run("   ")

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            ClaudeCliConfig(timeout_s=0)
        with self.assertRaises(ValueError):
            ClaudeCliConfig(model="")
        with self.assertRaises(ValueError):
            ClaudeCliConfig(effort="")


class TestFailures(AdapterTestCase):
    def assertFailure(self, result, error_class):
        self.assertFalse(result.success)
        self.assertEqual(result.error_class, error_class)
        self.assertIsNone(result.result_text)
        self.assertTrue(result.error_message)

    def test_malformed_json(self):
        r = self.adapter(FakeRunner(infer=Proc("not json {", "", 0, False))).run("hi")
        self.assertFailure(r, ErrorClass.BAD_OUTPUT)
        self.assertEqual(r.stdout, "not json {")

    def test_json_that_is_not_an_object(self):
        self.assertFailure(self.adapter(FakeRunner(infer=Proc("[1, 2]", "", 0, False))).run("hi"), ErrorClass.BAD_OUTPUT)

    def test_empty_result(self):
        for empty in ("", "   ", None):
            with self.subTest(result=empty):
                r = self.adapter(FakeRunner(infer=Proc(good_json(result=empty), "", 0, False))).run("hi")
                self.assertFailure(r, ErrorClass.BAD_OUTPUT)

    def test_is_error_true(self):
        r = self.adapter(FakeRunner(infer=Proc(good_json(is_error=True, result="something"), "", 0, False))).run("hi")
        self.assertFailure(r, ErrorClass.BAD_OUTPUT)

    def test_subtype_not_success(self):
        r = self.adapter(FakeRunner(infer=Proc(good_json(subtype="error_max_turns"), "", 0, False))).run("hi")
        self.assertFailure(r, ErrorClass.BAD_OUTPUT)

    def test_non_zero_exit(self):
        r = self.adapter(FakeRunner(infer=Proc(good_json(), "boom on stderr", 1, False))).run("hi")
        self.assertFailure(r, ErrorClass.ERROR)
        self.assertEqual(r.exit_code, 1)
        self.assertEqual(r.stderr, "boom on stderr")

    def test_timeout(self):
        r = self.adapter(FakeRunner(infer=Proc("", "", None, True))).run("hi")
        self.assertFailure(r, ErrorClass.TIMEOUT)
        self.assertTrue(r.timed_out)
        self.assertIsNone(r.exit_code)

    def test_no_automatic_retry(self):
        for infer in (Proc("", "", 1, False), Proc("", "", None, True), Proc("nope", "", 0, False)):
            with self.subTest(infer=infer):
                runner = FakeRunner(infer=infer)
                self.adapter(runner).run("hi")
                self.assertEqual(len(runner.inference_calls()), 1)

    def test_failure_can_raise_explicitly(self):
        r = self.adapter(FakeRunner(infer=Proc("", "", 2, False))).run("hi")
        with self.assertRaises(ClaudeCliError) as cm:
            r.raise_for_failure()
        self.assertEqual(cm.exception.error_class, ErrorClass.ERROR)


class TestCliMissing(AdapterTestCase):
    def test_executable_not_on_path(self):
        runner = FakeRunner()
        with mock.patch.object(claude_cli.shutil, "which", return_value=None):
            r = self.adapter(runner).run("hi")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.UNAVAILABLE)
        self.assertEqual(runner.calls, [])

    def test_process_cannot_be_started(self):
        r = self.adapter(FakeRunner(version=FileNotFoundError("gone"))).run("hi")
        self.assertFalse(r.success)
        self.assertEqual(r.error_class, ErrorClass.UNAVAILABLE)

    def test_version_command_fails(self):
        r = self.adapter(FakeRunner(version=Proc("", "x", 3, False))).run("hi")
        self.assertEqual(r.error_class, ErrorClass.ERROR)


class TestAuthFailure(AdapterTestCase):
    def assertNoInference(self, runner, result):
        self.assertFalse(result.success)
        self.assertEqual(result.error_class, ErrorClass.NOT_AUTHENTICATED)
        self.assertEqual(runner.inference_calls(), [])

    def test_auth_command_non_zero(self):
        runner = FakeRunner(auth=Proc(json.dumps({"email": EMAIL}), f"error for {EMAIL}", 1, False))
        self.assertNoInference(runner, self.adapter(runner).run("hi"))

    def test_logged_out(self):
        runner = FakeRunner(auth=Proc(json.dumps({"loggedIn": False, "email": EMAIL}), "", 0, False))
        self.assertNoInference(runner, self.adapter(runner).run("hi"))

    def test_auth_output_not_json(self):
        runner = FakeRunner(auth=Proc("Not signed in", "", 0, False))
        self.assertNoInference(runner, self.adapter(runner).run("hi"))

    def test_auth_check_timeout(self):
        runner = FakeRunner(auth=Proc("", "", None, True))
        r = self.adapter(runner).run("hi")
        self.assertEqual(r.error_class, ErrorClass.TIMEOUT)
        self.assertTrue(r.timed_out)
        self.assertEqual(runner.inference_calls(), [])

    def test_identifiers_never_leak(self):
        runner = FakeRunner(auth=Proc(json.dumps({"loggedIn": False, "email": EMAIL, "orgId": ORG_ID}),
                                      f"user {EMAIL} org {ORG_ID}", 1, False))
        with self.assertLogs("src.baseline.claude_cli", level="WARNING") as logs:
            r = self.adapter(runner).run("hi")
        with self.assertRaises(ClaudeCliError) as cm:
            r.raise_for_failure()
        everything = "\n".join([repr(r), str(cm.exception), *logs.output])
        self.assertNotIn(EMAIL, everything)
        self.assertNotIn(ORG_ID, everything)
        self.assertEqual(r.stdout, "")

    def test_redact_helper(self):
        self.assertEqual(claude_cli.redact(f"a {EMAIL} b {ORG_ID}"), "a [redacted-email] b [redacted-id]")


class TestRunProcess(unittest.TestCase):
    def test_normal_run_uses_no_shell_and_pipes_stdin(self):
        proc = mock.MagicMock()
        proc.communicate.return_value = ("out", "err")
        proc.returncode = 0
        with mock.patch.object(claude_cli.subprocess, "Popen", return_value=proc) as popen:
            out = claude_cli._run_process(["claude", "-p"], "prompt", 60, Path("."))
        self.assertEqual((out.stdout, out.stderr, out.returncode, out.timed_out), ("out", "err", 0, False))
        kwargs = popen.call_args.kwargs
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["encoding"], "utf-8")
        proc.communicate.assert_called_once_with(input="prompt", timeout=60)

    def test_no_stdin_text_means_no_interactive_input(self):
        proc = mock.MagicMock()
        proc.communicate.return_value = ("", "")
        proc.returncode = 0
        with mock.patch.object(claude_cli.subprocess, "Popen", return_value=proc) as popen:
            claude_cli._run_process(["claude", "-v"], None, 60, Path("."))
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_timeout_kills_process_and_flags_it(self):
        proc = mock.MagicMock()
        proc.communicate.side_effect = [subprocess.TimeoutExpired("claude", 60), ("partial", "late err")]
        proc.returncode = None
        with mock.patch.object(claude_cli.subprocess, "Popen", return_value=proc), \
                mock.patch.object(claude_cli, "_kill_tree") as kill:
            out = claude_cli._run_process(["claude"], "prompt", 60, Path("."))
        kill.assert_called_once_with(proc)
        self.assertTrue(out.timed_out)
        self.assertIsNone(out.returncode)
        self.assertEqual((out.stdout, out.stderr), ("partial", "late err"))


if __name__ == "__main__":
    unittest.main()
