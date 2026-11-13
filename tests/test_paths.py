"""TDD tests for the portable path resolution helpers in ``oc_selector.paths``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oc_selector.paths import global_agents_md_path

if TYPE_CHECKING:
    from pathlib import Path


class TestGlobalAgentsMdPath:
    """The global OpenCode guard path resolves under the config parent."""

    def test_happy_global_agents_md_path_resolves(
        self,
        isolated_home: Path,
    ) -> None:
        """Resolves to ``<isolated_home>/.config/opencode/AGENTS.md``.

        The path stays inside the isolated home (escaping ``deny_personal_home``
        otherwise) and mirrors opencode's own global config-dir base.
        """
        assert global_agents_md_path() == (
            isolated_home / ".config" / "opencode" / "AGENTS.md"
        )
