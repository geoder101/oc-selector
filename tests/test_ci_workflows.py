"""Minimal CI workflow policy tests for oc-selector.

These enforce the two invariants that keep the GitHub Actions workflows safe and
reproducible: every hosted action is pinned to an immutable commit SHA with a
matching release-tag comment, and no forbidden/insecure token leaks into any
workflow.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).parents[1]

# Immutable commit SHAs (with the release tag remembered in a trailing comment)
# that every hosted workflow must pin its actions to. Resolved from official
# releases; a floating major/version tag must never be used again.
EXPECTED_SHA: Final[dict[str, str]] = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv": "1db35dd8eee11c5894872e0b383ae6c406cf738d",
    "extractions/setup-just": "53165ef7e734c5c07cb06b3c8e7b647c5aa16db3",
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "actions/upload-pages-artifact": "fc324d3547104276b827a68afc52ff2a11cc49c9",
    "actions/deploy-pages": "368f82528645a54fb793d4d04e342629a3f51346",
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",
}

EXPECTED_TAG: Final[dict[str, str]] = {
    "actions/checkout": "v7.0.1",
    "astral-sh/setup-uv": "v10.4.0",
    "extractions/setup-just": "v4.0.0",
    "actions/upload-artifact": "v7.0.2",
    "actions/upload-pages-artifact": "v5.0.0",
    "actions/deploy-pages": "v5.0.1",
    "actions/download-artifact": "v8.0.2",
}

# Forbidden across hosted workflows: privileged/insecure triggers, leaked
# credentials, and external coverage reporters.
FORBIDDEN_TOKENS: Final[tuple[str, ...]] = (
    "continue-on-error",
    "secrets.",
    "pull_request_target",
    "workflow_run",
    "contents: write",
    "codecov",
)

# Workflows that legitimately require exactly one forbidden token for a bounded,
# documented reason. Each exception is scoped to a single token and grants nothing
# beyond it; no other workflow may use the token.
# - release writes a GitHub Release using the built-in token and contents: write.
TOKEN_EXCEPTIONS: Final[dict[str, tuple[str, ...]]] = {
    "release": ("contents: write", "secrets."),
}


def test_ci_release_pages_actions_are_sha_pinned() -> None:
    """Assert every hosted action across all workflows is SHA-pinned with a tag comment.

    Given: all hosted workflows, pinned to immutable commit SHAs rather than
    floating major/version tags. When: every action reference across all
    workflows is parsed. Then: each reference is the expected SHA with a matching
    tag comment, and no floating tag survives.
    """
    workflows = {
        "ci": (_ROOT / ".github/workflows/ci.yml").read_text(),
        "pages": (_ROOT / ".github/workflows/pages.yml").read_text(),
        "release": (_ROOT / ".github/workflows/release.yml").read_text(),
    }
    pattern = re.compile(r"uses:\s+([^/\s@]+/[^/\s@]+)@([0-9a-f]{40})(?:\s+#\s+(\S+))?")
    for name, workflow in workflows.items():
        references = list(pattern.finditer(workflow))
        assert references, f"{name} workflow references no actions"
        for action, ref, comment in (
            (m.group(1), m.group(2), m.group(3)) for m in references
        ):
            assert action in EXPECTED_SHA, f"{name}: unexpected action {action!r}"
            assert ref == EXPECTED_SHA[action], (
                f"{name}: {action} sha {ref!r} != {EXPECTED_SHA[action]}"
            )
            assert comment == EXPECTED_TAG[action], (
                f"{name}: {action} tag comment {comment!r} != {EXPECTED_TAG[action]}"
            )


def test_workflows_forbid_insecure_gating_and_leaked_credentials() -> None:
    """Assert no workflow uses a forbidden token outside its documented exception.

    Given: the hosted workflows, checked for insecure or leaky constructs.
    When/Then: no workflow may use privileged triggers, leaked secrets, external
    reporters, or insecure gating. Each workflow in TOKEN_EXCEPTIONS is granted
    only the single token it genuinely needs.
    """
    workflows = {
        "ci": (_ROOT / ".github/workflows/ci.yml").read_text(),
        "pages": (_ROOT / ".github/workflows/pages.yml").read_text(),
        "release": (_ROOT / ".github/workflows/release.yml").read_text(),
    }
    for name, workflow in workflows.items():
        exceptions = TOKEN_EXCEPTIONS.get(name, ())
        forbidden = tuple(t for t in FORBIDDEN_TOKENS if t not in exceptions)
        for token in forbidden:
            assert token not in workflow, f"{name}: forbidden {token!r} present"
