"""TDD tests for the pure ``agents_link`` symlink helpers.

Covers:
- ``link_path`` placement under the profile config dir
- ``is_identical_symlink`` true/false cases
- ``decide_link`` pure decision (ok / dangling / warn)
- ``ensure_link`` creation, dry-run no-op, already-identical no-op,
  dangling target, and preservation of a pre-existing real file
"""

from __future__ import annotations

from pathlib import Path

from oc_selector.agents_link import (
    decide_link,
    ensure_link,
    is_identical_symlink,
    link_path,
)


def _make_target(home: Path, *, exists: bool = True) -> Path:
    """Create (optionally) the global guard file and return its absolute path."""
    target = home / ".config" / "opencode" / "AGENTS.md"
    if exists:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("global guard\n")
    return target


def _profile_dir(home: Path, name: str = "work") -> Path:
    return home / "oc-selector" / "profiles" / name


class TestLinkPath:
    def test_link_path_places_agents_md_under_profile_dir(self) -> None:
        profile = _profile_dir(Path("/x/y/z"))
        assert link_path(profile) == profile / "AGENTS.md"


class TestIsIdenticalSymlink:
    def test_true_when_link_points_at_target(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_text("x")
        link = tmp_path / "link"
        link.symlink_to(str(target))
        assert is_identical_symlink(link, target) is True

    def test_false_when_link_points_elsewhere(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        other = tmp_path / "other"
        target.write_text("x")
        other.write_text("y")
        link = tmp_path / "link"
        link.symlink_to(str(other))
        assert is_identical_symlink(link, target) is False

    def test_false_when_not_a_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_text("x")
        real = tmp_path / "real"
        real.write_text("z")
        assert is_identical_symlink(real, target) is False

    def test_false_when_link_absent(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.write_text("x")
        assert is_identical_symlink(tmp_path / "missing", target) is False


class TestDecideLink:
    def test_ok_when_nothing_present(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        profile = _profile_dir(tmp_path)
        result = decide_link(profile, target)
        assert result.status == "ok"
        assert result.reason is None

    def test_ok_when_identical_symlink_exists(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        profile = _profile_dir(tmp_path)
        link = link_path(profile)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(str(target))
        result = decide_link(profile, target)
        assert result.status == "ok"

    def test_dangling_when_link_points_at_missing_target(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path, exists=False)
        profile = _profile_dir(tmp_path)
        link = link_path(profile)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(str(target))
        result = decide_link(profile, target)
        assert result.status == "dangling"

    def test_warn_when_pre_existing_real_file(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        profile = _profile_dir(tmp_path)
        link = link_path(profile)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text("user wrote their own guard\n")
        result = decide_link(profile, target)
        assert result.status == "warn"
        assert result.reason == "pre-existing-real-file"


class TestEnsureLink:
    def test_creates_absolute_symlink(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home)
        profile = _profile_dir(isolated_home)
        result = ensure_link(profile, target)
        assert result.status == "ok"
        link = link_path(profile)
        assert link.is_symlink() is True
        assert link.readlink().resolve() == target.resolve()

    def test_dry_run_is_noop(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home)
        profile = _profile_dir(isolated_home)
        result = ensure_link(profile, target, dry_run=True)
        assert result.status == "ok"
        link = link_path(profile)
        assert not link.is_symlink()
        assert not link.exists()

    def test_already_identical_is_noop(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home)
        profile = _profile_dir(isolated_home)
        first = ensure_link(profile, target)
        assert first.status == "ok"
        link = link_path(profile)
        original = str(link.readlink())
        second = ensure_link(profile, target)
        assert second.status == "ok"
        assert str(link.readlink()) == original

    def test_dangling_target_still_creates_link(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home, exists=False)
        profile = _profile_dir(isolated_home)
        result = ensure_link(profile, target)
        assert result.status == "dangling"
        link = link_path(profile)
        assert link.is_symlink() is True
        assert link.readlink().resolve() == target.resolve()

    def test_preserves_pre_existing_real_file(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home)
        profile = _profile_dir(isolated_home)
        link = link_path(profile)
        link.parent.mkdir(parents=True, exist_ok=True)
        original_bytes = b"user guard, must not be overwritten\n"
        link.write_bytes(original_bytes)
        result = ensure_link(profile, target)
        assert result.status == "warn"
        assert result.reason == "pre-existing-real-file"
        assert link.read_bytes() == original_bytes
        assert not link.is_symlink()

    def test_replaces_non_identical_symlink(self, isolated_home: Path) -> None:
        target = _make_target(isolated_home)
        profile = _profile_dir(isolated_home)
        link = link_path(profile)
        link.parent.mkdir(parents=True, exist_ok=True)
        other = _make_target(isolated_home, exists=False)
        link.symlink_to(str(other))
        result = ensure_link(profile, target)
        assert result.status == "ok"
        assert link.readlink().resolve() == target.resolve()
