"""Outcome-based regressions for transaction, launch and registry review findings."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import override

import pytest

from oc_selector.cli import main
from oc_selector.completion import generate_completion
from oc_selector.diagnostics import collect_diagnostics
from oc_selector.errors import MaintenanceError, TransactionError
from oc_selector.init_flow import run_recovery
from oc_selector.omo_upgrade import OmoSchema, run_omo_upgrade
from oc_selector.paths import registry_path, state_root
from oc_selector.process import build_launch_env, launch_native
from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml
from oc_selector.routing import NativeRoute, route
from oc_selector.transactions import (
    JournalPhase,
    Transaction,
    find_pending_journals,
    read_journal,
)
from oc_selector.wizard import (
    CreateProfileInput,
    EditProfileInput,
    run_create_wizard,
    run_edit_wizard,
)

pytestmark = pytest.mark.usefixtures("isolated_home")


class _InterruptedTransaction(Transaction):
    """Interrupt after applying destinations but before flushing COMMITTED."""

    @override
    def _write_journal(self) -> None:
        if self.phase == JournalPhase.COMMITTED:
            raise OSError("interrupted")
        super()._write_journal()


def _registry() -> Registry:
    """Build an isolated single-profile registry."""
    return Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="default")},
    )


def test_partial_apply_recovers_real_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover an interrupted transaction using only artifacts it actually produced."""
    root = tmp_path / "state"
    source = tmp_path / "source"
    source.write_text("new")
    first = tmp_path / "first"
    first.write_text("old")
    second = tmp_path / "second"
    tx = Transaction(root, "test")
    tx.begin()
    tx.stage_file(source, first)
    tx.stage_file(source, second)
    original = Path.replace

    def replace(path: Path, target: Path) -> Path:
        if target == second:
            raise OSError("injected second-write failure")
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", replace)
        with pytest.raises(OSError, match="injected"):
            tx.commit()
    tx.close()
    assert first.read_text() == "new"
    result = run_recovery(root / "journals" / f"{tx.id}.json", root)
    assert result.recovered
    assert first.read_text() == "old"
    assert not second.exists()
    assert find_pending_journals(root) == []


def test_failed_journal_flush_preserves_previous_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed transition cannot truncate the last durable recovery record."""
    root = tmp_path / "state"
    tx = Transaction(root, "test")
    tx.begin()
    path = root / "journals" / f"{tx.id}.json"
    before = path.read_bytes()

    def fail(_fd: int) -> None:
        raise OSError("injected fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError, match="injected"):
            tx.commit()
    tx.close()
    assert path.read_bytes() == before
    assert len(find_pending_journals(root)) == 1


def test_same_basename_resources_do_not_collide(tmp_path: Path) -> None:
    """Staging and backups identify full paths, including equal basenames."""
    destinations = [tmp_path / "a/config", tmp_path / "b/config"]
    sources = [tmp_path / "one", tmp_path / "two"]
    tx = Transaction(tmp_path / "state", "test")
    tx.begin()
    for index, (source, destination) in enumerate(
        zip(sources, destinations, strict=True)
    ):
        source.write_text(f"new-{index}")
        destination.parent.mkdir()
        destination.write_text(f"old-{index}")
        tx.stage_file(source, destination)
    tx.commit()
    assert [path.read_text() for path in destinations] == ["new-0", "new-1"]
    tx.rollback()
    assert [path.read_text() for path in destinations] == ["old-0", "old-1"]


def test_rollback_distinguishes_link_identity_from_equal_bytes(tmp_path: Path) -> None:
    """Equal content hashes cannot make a regular file stand in for an old link."""
    target = tmp_path / "target"
    target.write_text("referent")
    link = tmp_path / "link"
    link.symlink_to("target")
    source = tmp_path / "source"
    source.write_text("target")
    tx = Transaction(tmp_path / "state", "replace-link")
    tx.begin()
    tx.stage_file(source, link)
    tx.commit()
    assert not link.is_symlink()
    tx.rollback()
    assert link.is_symlink()
    assert link.readlink() == Path("target")
    assert target.read_text() == "referent"


def test_recovery_restores_dangling_symlink(tmp_path: Path) -> None:
    """Link recovery restores its original target even when both targets dangle."""
    root = tmp_path / "state"
    destination = tmp_path / "link"
    destination.symlink_to("old-missing")
    tx = _InterruptedTransaction(root, "link")
    tx.begin()
    tx.stage_symlink(destination, Path("new-missing"))
    with pytest.raises(OSError, match="interrupted"):
        tx.commit()
    tx.close()
    run_recovery(root / "journals" / f"{tx.id}.json", root)
    assert destination.is_symlink()
    assert destination.readlink() == Path("old-missing")


def test_recovery_refuses_unexpected_edits(tmp_path: Path) -> None:
    """Keep the journal and user edits when content no longer matches evidence."""
    root = tmp_path / "state"
    source = tmp_path / "source"
    source.write_text("new")
    destination = tmp_path / "destination"
    destination.write_text("old")
    tx = _InterruptedTransaction(root, "edit")
    tx.begin()
    tx.stage_file(source, destination)
    with pytest.raises(OSError, match="interrupted"):
        tx.commit()
    tx.close()
    tx_path = root / "journals" / f"{tx.id}.json"
    journal = read_journal(tx_path)
    assert journal is not None
    destination.write_text("user edits")
    with pytest.raises(TransactionError, match="Unexpected edits"):
        run_recovery(tx_path, root)
    assert destination.read_text() == "user edits"
    assert tx_path.exists()


def test_corrupt_journal_blocks_admission(tmp_path: Path) -> None:
    """Malformed recovery evidence is an error, never a clean state."""
    journals = tmp_path / "journals"
    journals.mkdir()
    (journals / "bad.json").write_text("{")
    with pytest.raises(TransactionError, match="Unreadable"):
        find_pending_journals(tmp_path)


@pytest.mark.parametrize("active", [True, False])
def test_launch_refuses_maintenance_or_pending_recovery(*, active: bool) -> None:
    """Both a held maintenance lock and an interrupted transaction block launch."""
    tx = Transaction(state_root(), "maintenance")
    tx.begin()
    if not active:
        tx.close()
    try:
        with pytest.raises(MaintenanceError):
            launch_native(NativeRoute(()), _registry(), dry_run=True)
    finally:
        tx.close()


def test_model_pin_merges_inline_overrides() -> None:
    """A model pin reaches OpenCode without losing inherited inline settings."""
    reg = Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="default", model="provider/model")},
    )
    env = build_launch_env(
        reg, "default", {"OPENCODE_CONFIG_CONTENT": '{"theme":"dark"}'}
    )
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {
        "model": "provider/model",
        "theme": "dark",
    }
    explicit = build_launch_env(
        reg, "default", {"OPENCODE_CONFIG_CONTENT": '{"model":"other/model"}'}
    )
    assert json.loads(explicit["OPENCODE_CONFIG_CONTENT"])["model"] == "other/model"


def test_create_uses_snapshot_loaded_under_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful intervening create is not overwritten by a stale snapshot."""
    path = registry_path()
    path.parent.mkdir(parents=True)
    path.write_text(registry_to_toml(_registry()))
    original = Transaction.begin

    def interleaved_begin(tx: Transaction) -> None:
        original(tx)
        # Model another writer having committed just before lock acquisition.
        reg = Registry(
            default_profile="default",
            profiles={
                **_registry().profiles,
                "intervening": ProfileEntry(directory="intervening"),
            },
        )
        path.write_text(registry_to_toml(reg))

    monkeypatch.setattr(Transaction, "begin", interleaved_begin)
    result = run_create_wizard(CreateProfileInput("new", "new"), path, path.parent)
    assert result.success
    assert set(load_registry(path).profiles) == {"default", "intervening", "new"}


@pytest.mark.parametrize("dry_run", [True, False])
def test_duplicate_directories_are_classified(*, dry_run: bool) -> None:
    """Create/edit previews and real mutations reject duplicate directories alike."""
    path = registry_path()
    path.parent.mkdir(parents=True)
    reg = Registry(
        default_profile="default",
        profiles={
            **_registry().profiles,
            "second": ProfileEntry(directory="second"),
        },
    )
    path.write_text(registry_to_toml(reg))
    created = run_create_wizard(
        CreateProfileInput("new", "default"), path, path.parent, dry_run=dry_run
    )
    edited = run_edit_wizard(
        EditProfileInput("second", directory="default"),
        path,
        path.parent,
        dry_run=dry_run,
    )
    assert not created.success
    assert not edited.success
    assert load_registry(path) == reg


@pytest.mark.parametrize("status", [37, 130])
def test_main_propagates_native_status(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The console entry point preserves supervised native exit statuses."""
    path = registry_path()
    path.parent.mkdir(parents=True)
    path.write_text(registry_to_toml(_registry()))

    def launch(*_args: object, **_kwargs: object) -> int:
        return status

    monkeypatch.setattr("oc_selector.cli.launch_native", launch)
    monkeypatch.setattr(sys, "argv", ["oc-selector"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == status


def test_recovery_command_bypasses_corrupt_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable registry must not prevent reaching the recovery handler."""
    path = registry_path()
    path.parent.mkdir(parents=True)
    path.write_text("broken = [")
    monkeypatch.setattr(sys, "argv", ["oc-selector", "init", "--recover"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0


def test_invalid_omo_config_leaves_no_plaintext(tmp_path: Path) -> None:
    """Validation failure does not create a staging file or state directory."""
    state = tmp_path / "state"
    result = run_omo_upgrade(
        tmp_path / "omo",
        OmoSchema("2", ("version",)),
        state,
        {"version": "wrong", "credential": "private"},
    )
    assert not result.success
    assert not state.exists()


def test_diagnostics_excludes_completed_journals() -> None:
    """A successful transaction is clean even though its journal is retained."""
    tx = Transaction(state_root(), "test")
    tx.begin()
    tx.commit()

    def probe(_executable: str, _timeout: float) -> tuple[str | None, str]:
        return "test", "ok"

    result = collect_diagnostics(_registry(), version_probe=probe)
    assert result.recovery == {"status": "clean", "transactions": []}


def test_launch_refreshes_default_under_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A default change between routing and admission selects the current default."""
    old = _registry()
    decision = route([], old)
    assert isinstance(decision, NativeRoute)
    current = Registry(
        default_profile="second",
        opencode_executable=sys.executable,
        profiles={**old.profiles, "second": ProfileEntry(directory="second")},
    )
    path = registry_path()
    path.parent.mkdir(parents=True)
    path.write_text(registry_to_toml(current))
    launched: list[dict[str, str]] = []

    def launch(_argv: list[str], env: dict[str, str]) -> int:
        launched.append(env)
        return 0

    monkeypatch.setattr("oc_selector.process._launch_posix", launch)
    monkeypatch.setattr("oc_selector.process._launch_windows", launch)
    assert launch_native(decision, old) == 0
    assert Path(launched[0]["OPENCODE_CONFIG_DIR"]).name == "second"


@pytest.mark.console
@pytest.mark.parametrize("shell", ["powershell", "zsh"])
def test_generated_completion_registers_in_real_shell(
    tmp_path: Path, shell: str
) -> None:
    """Install the actual generated script, without compensating registration."""
    executable = shutil.which("pwsh" if shell == "powershell" else shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    script = generate_completion(shell, _registry(), {"create": ["--no-interactive"]})
    path = tmp_path / ("completion.ps1" if shell == "powershell" else "completion.zsh")
    path.write_text(script)
    if shell == "powershell":
        text = "oc-selector create --n"
        quoted = str(path).replace("'", "''")
        command = (
            f"$ErrorActionPreference='Stop'; . '{quoted}'; "
            f"$r=TabExpansion2 '{text}' {len(text)}; "
            "if ($r.CompletionMatches.CompletionText -notcontains '--no-interactive') "
            "{ throw 'completion missing' }"
        )
        argv = [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
    else:
        argv = [
            executable,
            "-f",
            "-c",
            (
                'autoload -Uz compinit; compinit -D; source "$1"; '
                '[[ "${_comps[oc-selector]}" == _oc-selector_completions ]]'
            ),
            "test",
            str(path),
        ]
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, result.stderr
