"""Manual smoke test: ONE real Claude CLI call through the Baseline adapter.

Not part of the automated test suite (it makes a real inference call).
Run from the project root:

    python -m tests.manual.smoke_claude_cli
    python -m tests.manual.smoke_claude_cli "Return exactly OK."

Exit code 0 on success, 1 on failure. Prints a summary without account identifiers.
"""
import json
import logging
import sys

from src.baseline.claude_cli import ClaudeCliAdapter

PROMPT = "Return exactly OK."


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    prompt = argv[1] if len(argv) > 1 else PROMPT
    adapter = ClaudeCliAdapter()  # adapter defaults: model claude-sonnet-5, effort low
    result = adapter.run(prompt)
    summary = {
        "configured_model": adapter.config.model,
        "configured_effort": adapter.config.effort,
        "success": result.success,
        "result_text": result.result_text,
        "model": result.model,
        "models_used": list(result.models_used),
        "cli_version": result.cli_version,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stderr": result.stderr,
        "timing": result.timing,
        "error_class": result.error_class.value if result.error_class else None,
        "error_message": result.error_message,
        "argv": list(result.argv),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
