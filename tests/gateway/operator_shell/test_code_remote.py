"""Tests for gateway.operator_shell.code_remote.

code_remote drives Claude Code runs from Telegram. Before this file it had zero
tests, which is how ``dict.fromkeys(paths)[:4]`` shipped: a dict is not
sliceable, so every task card naming a source file raised instead of rendering.
``test_render_task_card_survives_result_with_file_paths`` is that regression.
"""

from __future__ import annotations

import sqlite3

import pytest

from gateway.operator_shell.code_remote import (
    detect_fence,
    format_progress_card,
    is_code_command,
    is_natural_code_assign,
    is_task_query,
    parse_steer,
    render_task_card,
)

# --------------------------------------------------------------------------
# Fake coordinator — real sqlite3.Row rows, no estate/daemon dependency.
# --------------------------------------------------------------------------

_COLUMNS = (
    "id TEXT, status TEXT, title TEXT, result TEXT, "
    "last_failure_error TEXT, created_at REAL, body TEXT, risk_class TEXT"
)


class _FakeCoordinator:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(f"CREATE TABLE tasks ({_COLUMNS})")
        for row in self._rows:
            conn.execute(
                "INSERT INTO tasks (id, status, title, result, "
                "last_failure_error, created_at, body, risk_class) "
                "VALUES (:id, :status, :title, :result, :last_failure_error, "
                ":created_at, :body, :risk_class)",
                {
                    "id": row.get("id", "a" * 32),
                    "status": row.get("status", "done"),
                    "title": row.get("title", "CODE: something"),
                    "result": row.get("result", ""),
                    "last_failure_error": row.get("last_failure_error", ""),
                    "created_at": row.get("created_at", 0.0),
                    "body": row.get("body", ""),
                    "risk_class": row.get("risk_class", "low"),
                },
            )
        conn.commit()
        return conn

    def _circuit_breaker_status(self, _name):
        return True


@pytest.fixture
def fake_coord(monkeypatch):
    def _install(rows):
        coord = _FakeCoordinator(rows)
        monkeypatch.setattr(
            "gateway.operator_shell.code_remote._coord", lambda: coord
        )
        return coord

    return _install


# --------------------------------------------------------------------------
# F3 regression — the crash that reached production
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        "patched gateway/run.py and tests/test_x.py plus docs/readme.md",
        "touched ../../executor-settings.js",  # the first real-world crasher
        "edited a.py b.ts c.tsx d.go e.rs f.md g.yml",  # more than the 4 shown
        "same file twice: gateway/run.py and gateway/run.py",  # dedupe path
    ],
)
def test_render_task_card_survives_result_with_file_paths(fake_coord, result):
    """A result naming source files must render, not raise.

    Slicing ``dict.fromkeys(...)`` raised TypeError/KeyError depending on the
    interpreter; either way the operator's status button 500'd.
    """
    fake_coord([{"id": "deadbeef" + "0" * 24, "status": "done", "result": result}])
    text, buttons = render_task_card("deadbeef")
    assert "deadbeef" in text
    assert buttons


def test_render_task_card_lists_at_most_four_deduped_files(fake_coord):
    fake_coord(
        [
            {
                "id": "cafe1234" + "0" * 24,
                "status": "done",
                "result": "a.py a.py b.py c.py d.py e.py f.py",
            }
        ]
    )
    text, _ = render_task_card("cafe1234")
    files_line = next(ln for ln in text.splitlines() if ln.startswith("Files:"))
    assert files_line.count(",") == 3  # 4 entries
    assert "a.py" in files_line and files_line.count("a.py") == 1


def test_render_task_card_unknown_ref_is_graceful(fake_coord):
    fake_coord([])
    text, buttons = render_task_card("nosuchid")
    assert "No task" in text
    assert buttons


# --------------------------------------------------------------------------
# Command parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("cc fix the parser", "fix the parser"),
        ("code: fix the parser", "fix the parser"),
        ("Otto code fix the parser", "fix the parser"),
        ("Otto, claude code - fix the parser", "fix the parser"),
    ],
)
def test_is_code_command_extracts_body(text, expected):
    assert is_code_command(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "cc", "cc ab", "just chatting"])
def test_is_code_command_rejects_non_commands(text):
    assert is_code_command(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("task 3b326b72", "3b326b72"),
        ("Otto task `deadbeef`?", "deadbeef"),
        ("run 1a2b", "1a2b"),
    ],
)
def test_is_task_query_extracts_id(text, expected):
    assert is_task_query(text) == expected


def test_is_task_query_rejects_prose():
    assert is_task_query("what tasks are running") is None


def test_parse_steer_splits_id_and_instruction():
    assert parse_steer("Otto steer 3b326b72 only touch tests") == (
        "3b326b72",
        "only touch tests",
    )


def test_parse_steer_rejects_missing_instruction():
    assert parse_steer("steer 3b326b72") is None


# --------------------------------------------------------------------------
# The money/identity fence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("wire up stripe payouts", "money"),
        ("fix the settlement job", "money"),
        ("update KYC checks", "identity"),
        ("patch the TIE introduction exchange", "identity"),
        ("rename a button", None),
    ],
)
def test_detect_fence_classifies_risk(body, expected):
    assert detect_fence(body) == expected


def test_detect_fence_is_keyword_only_and_misses_paths():
    """Documents a known gap: the fence reads words, not blast radius.

    A checkout-path edit that never says "money" or "stripe" is NOT fenced.
    If this ever starts returning "money", the fence got smarter — update it.
    """
    assert detect_fence("fix the buy button in store_platform") is None


# --------------------------------------------------------------------------
# Natural-language assignment
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "fix the login bug in prospector",
        "Otto, implement dark mode on POPDD",
        "refactor the funnel in prospector and add tests",
    ],
)
def test_is_natural_code_assign_accepts_anchored_asks(text):
    assert is_natural_code_assign(text)


@pytest.mark.parametrize(
    "text",
    [
        "fix it",  # too short, no anchor
        "how is prospector doing",  # not a coding verb
        "fix the thing",  # no repo anchor
        "x" * 501,  # too long
    ],
)
def test_is_natural_code_assign_rejects_ambiguous(text):
    assert is_natural_code_assign(text) is None


# --------------------------------------------------------------------------
# Card formatting
# --------------------------------------------------------------------------


def test_format_progress_card_includes_phase_and_blocker():
    card = format_progress_card(
        "abcdef1234", "CODE: thing", phase="executing", blocker="quota"
    )
    assert "abcdef12" in card
    assert "executing" in card
    assert "quota" in card


def test_format_progress_card_truncates_long_title():
    card = format_progress_card("abcdef1234", "T" * 500, phase="queued")
    assert len(max(card.splitlines(), key=len)) < 200
