"""Cross-platform evidence runner for oc-selector verification.

Usage::

    uv run python tools/verify.py --evidence PATH [--expect-exit N] -- COMMAND ...

Captures the command's argv, UTC start/finish timestamps, stdout/stderr and the
actual return code, redacts fixture secrets, writes the evidence to ``PATH`` and
preserves the child's failure status. With ``--expect-exit N`` the run is
considered successful only when the child exits with exactly ``N`` (an expected
failure); any other result fails the recorder. This never turns a failing gate
green: it only records that a *specific* expected failure occurred.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class _CliArgs(BaseModel):
    """Typed view over the parsed argparse options.

    ``argparse.Namespace`` attributes are untyped, so the parsed namespace is
    validated through this model to obtain a typed boundary.
    """

    evidence: str
    expect_exit: int | None = None


# Built-in fixture secret canaries that must never appear in evidence.
_BUILTIN_SECRETS = (
    "ocx-canary-api-key-0001",
    "ocx-canary-token-0002",
    "ocx-canary-password-0003",
)

# Patterns for common secret shapes.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[=:]\s*\S+"),
)


def _explicit_secrets() -> tuple[str, ...]:
    """Return caller-supplied secrets from the OCX_REDACT_SECRETS env var."""
    raw = os.environ.get("OCX_REDACT_SECRETS", "")
    return tuple(item for item in raw.split(",") if item)


def redact(text: str) -> str:
    """Redact known fixture secrets and common secret shapes from text."""
    for secret in _BUILTIN_SECRETS + _explicit_secrets():
        text = text.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _ensure_newline(text: str) -> str:
    """Return text guaranteed to end with a newline (if non-empty)."""
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first ``--`` into (options, command)."""
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def main() -> int:
    """Run the command, capture evidence, and return the appropriate exit code."""
    opts, command = _split_command(sys.argv[1:])

    parser = argparse.ArgumentParser(description="Evidence runner for oc-selector.")
    parser.add_argument(
        "--evidence",
        required=True,
        help="Path to write the evidence file.",
    )
    parser.add_argument(
        "--expect-exit",
        type=int,
        default=None,
        help="Expected child exit code.",
    )
    ns = parser.parse_args(opts)
    args = _CliArgs.model_validate(vars(ns))
    evidence_path = Path(args.evidence)
    expect_exit = args.expect_exit

    if not command:
        sys.stderr.write("error: no command supplied after '--'\n")
        return 2

    started = _utc_now()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    finished = _utc_now()
    expectation = (
        "n/a"
        if expect_exit is None
        else "matched"
        if completed.returncode == expect_exit
        else "not-matched"
    )

    stdout_red = _ensure_newline(redact(completed.stdout))
    stderr_red = _ensure_newline(redact(completed.stderr))
    evidence = "\n".join(
        (
            "=== oc-selector verify evidence ===",
            f"command: {json.dumps(command)}",
            f"started_utc: {started}",
            f"finished_utc: {finished}",
            f"return_code: {completed.returncode}",
            f"expected_exit: {expect_exit if expect_exit is not None else 'none'}",
            f"expectation: {expectation}",
            "",
            "--- stdout (redacted) ---",
            stdout_red,
            "--- stderr (redacted) ---",
            stderr_red,
        ),
    )

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence, encoding="utf-8")

    sys.stdout.write(stdout_red)
    sys.stderr.write(stderr_red)

    if expect_exit is not None:
        return 0 if completed.returncode == expect_exit else 1
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
