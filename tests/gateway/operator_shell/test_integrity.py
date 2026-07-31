"""Tests for the untracked-module fence.

The incident: estate.py imported gateway.operator_shell.daemons while
daemons.py was untracked — unreviewed code with launchctl bootout powers, one
gateway restart from live. These tests pin both the detection and the
deliberate choice to warn rather than deny by default.
"""

from __future__ import annotations

import subprocess

import pytest

from gateway.operator_shell import integrity


def _git_repo(tmp_path):
    pkg = tmp_path / "operator_shell"
    pkg.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return pkg


def _commit(tmp_path, *names):
    for n in names:
        subprocess.run(["git", "add", f"operator_shell/{n}"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=tmp_path, check=True)


def test_ghost_modules_flags_untracked_file(tmp_path):
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py")
    (pkg / "daemons.py").write_text("import subprocess\n", encoding="utf-8")

    assert integrity.ghost_modules(pkg) == ["daemons"]


def test_ghost_modules_clean_when_everything_tracked(tmp_path):
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "daemons.py").write_text("x = 2\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py", "daemons.py")

    assert integrity.ghost_modules(pkg) == []


def test_ghost_modules_ignores_dunder_init(tmp_path):
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py")
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    assert integrity.ghost_modules(pkg) == []


def test_ghost_modules_returns_empty_when_git_cannot_answer(tmp_path):
    """No git repo -> no finding, and no false clean bill either (caller logs)."""
    pkg = tmp_path / "operator_shell"
    pkg.mkdir()
    (pkg / "daemons.py").write_text("x = 1\n", encoding="utf-8")

    assert integrity.ghost_modules(pkg) == []


def test_enforce_warns_by_default_and_does_not_raise(tmp_path, caplog):
    """Denying by default would take the panel down at the next restart."""
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py")
    (pkg / "daemons.py").write_text("x = 2\n", encoding="utf-8")

    with caplog.at_level("ERROR"):
        assert integrity.enforce(pkg) == ["daemons"]
    assert "UNREVIEWED" in caplog.text
    assert "daemons.py" in caplog.text


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_enforce_is_fatal_in_strict_mode(tmp_path, monkeypatch, flag):
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py")
    (pkg / "daemons.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_STRICT_TRACKED_IMPORTS", flag)

    with pytest.raises(RuntimeError, match="UNREVIEWED"):
        integrity.enforce(pkg)


def test_enforce_silent_when_clean(tmp_path, monkeypatch, caplog):
    pkg = _git_repo(tmp_path)
    (pkg / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "tracked.py")
    monkeypatch.setenv("HERMES_STRICT_TRACKED_IMPORTS", "1")

    with caplog.at_level("ERROR"):
        assert integrity.enforce(pkg) == []
    assert caplog.text == ""


def test_live_package_reports_its_own_ghosts():
    """Smoke: runs against the real package. Asserts the shape, not the count."""
    assert isinstance(integrity.ghost_modules(), list)
