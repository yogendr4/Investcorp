"""Baseline Claude Code CLI adapter.

Python application -> Claude Code CLI (subprocess) -> structured result.

The only place where the application talks to the CLI. It verifies the CLI and
its login, runs one non-interactive call with the prompt on stdin, and returns a
CliResult. It never retries and never raises on CLI failure; failures come back
as CliResult(success=False, error_class=...). Use CliResult.raise_for_failure()
for exception-style handling. Contract: docs/architecture/baseline_contract.md,
section 21. Standard library only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXECUTABLE = "claude"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "low"
DEFAULT_TIMEOUT_S = 60.0

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
_TIMING_KEYS = ("duration_ms", "duration_api_ms", "ttft_ms")


class ErrorClass(str, Enum):
    UNAVAILABLE = "LLM_CLI_UNAVAILABLE"
    NOT_AUTHENTICATED = "LLM_CLI_NOT_AUTHENTICATED"
    TIMEOUT = "LLM_CLI_TIMEOUT"
    ERROR = "LLM_CLI_ERROR"
    BAD_OUTPUT = "LLM_CLI_BAD_OUTPUT"


@dataclass(frozen=True)
class ClaudeCliConfig:
    executable: str = DEFAULT_EXECUTABLE
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    timeout_s: float = DEFAULT_TIMEOUT_S
    working_dir: Path = PROJECT_ROOT

    def __post_init__(self) -> None:
        if not self.executable or not self.model or not self.effort:
            raise ValueError("executable, model and effort must be non-empty")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        object.__setattr__(self, "working_dir", Path(self.working_dir))


@dataclass(frozen=True)
class CliResult:
    success: bool
    result_text: Optional[str] = None
    model: Optional[str] = None
    models_used: tuple[str, ...] = ()
    cli_version: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error_class: Optional[ErrorClass] = None
    error_message: Optional[str] = None
    timing: dict[str, float] = field(default_factory=dict)
    argv: tuple[str, ...] = ()

    def raise_for_failure(self) -> "CliResult":
        if not self.success:
            raise ClaudeCliError(self.error_class or ErrorClass.ERROR, self.error_message or "Claude CLI call failed")
        return self


class ClaudeCliError(RuntimeError):
    def __init__(self, error_class: ErrorClass, message: str) -> None:
        super().__init__(f"{error_class.value}: {message}")
        self.error_class = error_class


@dataclass(frozen=True)
class _ProcOutput:
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool


def redact(text: str) -> str:
    """Remove email addresses and UUID-style identifiers from text."""
    return _UUID_RE.sub("[redacted-id]", _EMAIL_RE.sub("[redacted-email]", text or ""))


def inference_args(config: ClaudeCliConfig) -> list[str]:
    """Arguments after the executable for the approved non-interactive call."""
    return ["-p", "--output-format", "json", "--model", config.model, "--effort", config.effort, "--tools", "",
            "--permission-prompts", "none", "--no-session-persistence", "--safe-mode"]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10, check=False)
        proc.kill()
    except Exception:  # best effort; the caller already reports the timeout
        pass


def _run_process(args: list[str], stdin_text: Optional[str], timeout_s: float, cwd: Path) -> _ProcOutput:
    """Run one process, capture stdout/stderr, enforce the timeout. Never uses a shell."""
    proc = subprocess.Popen(
        args, cwd=str(cwd), text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout_s)
        return _ProcOutput(out or "", err or "", proc.returncode, False)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return _ProcOutput(out or "", err or "", None, True)


class ClaudeCliAdapter:
    def __init__(self, config: Optional[ClaudeCliConfig] = None) -> None:
        self.config = config or ClaudeCliConfig()
        self._resolved: Optional[str] = None
        self._cli_version: Optional[str] = None
        self._ready = False

    # ---- public API ----
    def check_prerequisites(self) -> CliResult:
        """CLI present, `claude -v` works, and `claude auth status --json` reports loggedIn."""
        cfg = self.config
        self._ready = False
        resolved = shutil.which(cfg.executable)
        if resolved is None:
            return self._fail(ErrorClass.UNAVAILABLE, f"Claude CLI executable not found: {cfg.executable!r}", argv=(cfg.executable,))
        self._resolved = resolved

        v_argv = (cfg.executable, "-v")
        out, fail = self._call([resolved, "-v"], None, v_argv, redact_output=True)
        if fail:
            return fail
        if out.returncode != 0:
            return self._fail(ErrorClass.ERROR, f"'claude -v' exited with code {out.returncode}", argv=v_argv,
                              exit_code=out.returncode, stderr=redact(out.stderr))
        version = out.stdout.strip()
        if not version:
            return self._fail(ErrorClass.BAD_OUTPUT, "'claude -v' printed nothing", argv=v_argv, exit_code=out.returncode)

        a_argv = (cfg.executable, "auth", "status", "--json")
        out, fail = self._call([resolved, "auth", "status", "--json"], None, a_argv, redact_output=True, version=version)
        if fail:
            return fail
        if out.returncode != 0:
            return self._fail(ErrorClass.NOT_AUTHENTICATED, f"authentication check failed (exit code {out.returncode})", argv=a_argv,
                              exit_code=out.returncode, stderr=redact(out.stderr), version=version)
        try:
            status = json.loads(out.stdout)
        except ValueError:
            status = None
        if not isinstance(status, dict) or status.get("loggedIn") is not True:
            return self._fail(ErrorClass.NOT_AUTHENTICATED, "not logged in, or authentication status could not be verified", argv=a_argv,
                              exit_code=out.returncode, version=version)

        self._cli_version = version
        self._ready = True
        return CliResult(success=True, cli_version=version, exit_code=0, argv=a_argv)

    def run(self, prompt: str) -> CliResult:
        """One inference call. Prompt goes to stdin. No retries."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not self._ready:
            pre = self.check_prerequisites()
            if not pre.success:
                return pre
        cfg = self.config
        argv = (cfg.executable, *inference_args(cfg))
        start = time.monotonic()
        out, fail = self._call([self._resolved, *inference_args(cfg)], prompt, argv, version=self._cli_version)
        if fail:
            return fail
        wall_ms = round((time.monotonic() - start) * 1000, 1)
        common = dict(argv=argv, exit_code=out.returncode, stdout=out.stdout, stderr=out.stderr, version=self._cli_version)
        if out.returncode != 0:
            return self._fail(ErrorClass.ERROR, f"Claude CLI exited with code {out.returncode}", **common)
        try:
            data = json.loads(out.stdout)
        except ValueError:
            return self._fail(ErrorClass.BAD_OUTPUT, "stdout is not valid JSON", **common)
        if not isinstance(data, dict):
            return self._fail(ErrorClass.BAD_OUTPUT, "stdout JSON is not an object", **common)
        if data.get("is_error"):
            return self._fail(ErrorClass.BAD_OUTPUT, "CLI reported is_error=true", **common)
        if data.get("subtype") != "success":
            return self._fail(ErrorClass.BAD_OUTPUT, f"CLI subtype is {data.get('subtype')!r}, not 'success'", **common)
        text = data.get("result")
        if not isinstance(text, str) or not text.strip():
            return self._fail(ErrorClass.BAD_OUTPUT, "CLI result is empty", **common)

        timing: dict[str, float] = {"wall_clock_ms": wall_ms}
        for key in _TIMING_KEYS:
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                timing[key] = data[key]
        usage = data.get("modelUsage")
        models = tuple(usage) if isinstance(usage, dict) else ()
        model = cfg.model if cfg.model in models else (models[0] if len(models) == 1 else None)
        logger.info("claude cli call ok: wall_clock_ms=%s", wall_ms)
        return CliResult(success=True, result_text=text, model=model, models_used=models, cli_version=self._cli_version,
                         exit_code=out.returncode, stdout=out.stdout, stderr=out.stderr, timing=timing, argv=argv)

    # ---- internals ----
    def _call(self, args: list[str], stdin_text: Optional[str], argv: tuple[str, ...], *,
              redact_output: bool = False, version: Optional[str] = None):
        try:
            out = _run_process(args, stdin_text, self.config.timeout_s, self.config.working_dir)
        except OSError as exc:
            return None, self._fail(ErrorClass.UNAVAILABLE, f"Claude CLI could not be started ({type(exc).__name__})", argv=argv, version=version)
        if out.timed_out:
            err = redact(out.stderr) if redact_output else out.stderr
            return None, self._fail(ErrorClass.TIMEOUT, f"Claude CLI call exceeded {self.config.timeout_s:g} seconds", argv=argv, stdout=("" if redact_output else out.stdout),
                                    stderr=err, version=version, timed_out=True)
        return out, None

    def _fail(self, error_class: ErrorClass, message: str, *, argv: tuple[str, ...] = (), exit_code: Optional[int] = None,
              stdout: str = "", stderr: str = "", version: Optional[str] = None, timed_out: bool = False) -> CliResult:
        logger.warning("claude cli failure: class=%s exit_code=%s timed_out=%s", error_class.value, exit_code, timed_out)
        return CliResult(success=False, cli_version=version or self._cli_version, exit_code=exit_code, stdout=stdout, stderr=stderr,
                         timed_out=timed_out, error_class=error_class, error_message=message, argv=argv)
