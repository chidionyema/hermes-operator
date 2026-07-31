"""Operator shell unit tests — mission, inbox, natural ops, proof, menu."""

from __future__ import annotations

from gateway.operator_shell.cron_ops import format_cron_command
from gateway.operator_shell.menu import (
    OPERATOR_TELEGRAM_MENU,
    filter_operator_menu,
    resolve_telegram_menu_profile,
)
from gateway.operator_shell.natural_ops import match_natural_op
from gateway.operator_shell.proof import (
    check_idempotent,
    new_request_id,
    push_undo,
    pop_undo,
    store_idempotent,
)
from gateway.operator_shell.voice_brief import wants_executive_brief


def test_operator_menu_is_twelve_or_fewer():
    assert len(OPERATOR_TELEGRAM_MENU) <= 12
    assert "panel" in OPERATOR_TELEGRAM_MENU
    assert "cron" in OPERATOR_TELEGRAM_MENU


def test_filter_operator_menu_uses_tier0_order_not_input_order():
    # filter_operator_menu emits OPERATOR_TELEGRAM_MENU order (menu.py:49),
    # not the caller's order, and drops anything not Tier-0 ("zzz", "new").
    cmds = [("zzz", "Z"), ("panel", "Panel"), ("help", "Help"), ("cron", "Cron"), ("new", "New")]
    assert [n for n, _ in filter_operator_menu(cmds)] == ["panel", "cron", "help"]


def test_filter_operator_menu_drops_non_tier0():
    assert filter_operator_menu([("zzz", "Z"), ("new", "New")]) == []


def test_resolve_menu_profile_operator():
    assert resolve_telegram_menu_profile({"operator_shell": {"menu_profile": "operator"}}) == "operator"
    assert resolve_telegram_menu_profile({}) == "default"


def test_cron_help_mentions_list(monkeypatch):
    monkeypatch.setattr(
        "gateway.operator_shell.cron_ops._cron_api",
        lambda **kwargs: {"success": True, "jobs": []},
    )
    text = format_cron_command("")
    assert "/cron list" in text


def test_cron_pause_formats(monkeypatch):
    def fake_api(**kwargs):
        assert kwargs.get("action") == "pause"
        return {"success": True, "job": {"name": "morning-brief"}}

    monkeypatch.setattr("gateway.operator_shell.cron_ops._cron_api", fake_api)
    assert "Paused" in format_cron_command("pause abc123")


def test_panel_fail_closed_without_coordinator(monkeypatch, tmp_path):
    from gateway.operator_shell import estate as estate_mod

    monkeypatch.setattr(estate_mod, "_hermes_home", lambda: tmp_path)
    estate_mod._COORD_CACHE = None
    estate_mod._COORD_ERROR = None
    view = estate_mod.render_panel_view()
    assert view.ok is False


def test_natural_ops_pause_spend():
    op = match_natural_op("pause spend")
    assert op is not None and op.action == "pause"
    assert match_natural_op("please rewrite the entire prospector pipeline") is None


def test_natural_ops_run_prospector_count():
    op = match_natural_op("run prospector 20")
    assert op is not None and op.action == "run_prospector" and op.args == "20"


def test_voice_brief_triggers():
    assert wants_executive_brief("status", from_voice=False)
    assert wants_executive_brief("how are we doing", from_voice=True)
    assert not wants_executive_brief("implement a new auth system please", from_voice=True)


def test_idempotent_callbacks(tmp_path, monkeypatch):
    from gateway.operator_shell import proof as proof_mod

    monkeypatch.setattr(proof_mod, "_hermes_home", lambda: tmp_path)
    rid = new_request_id()
    assert check_idempotent(rid) is None
    store_idempotent(rid, {"text": "ok", "buttons": []})
    assert check_idempotent(rid)["text"] == "ok"


def test_undo_stack(tmp_path, monkeypatch):
    from gateway.operator_shell import proof as proof_mod

    monkeypatch.setattr(proof_mod, "_hermes_home", lambda: tmp_path)
    token = push_undo("pause", {"set_paused": False}, "paused spend")
    rec = pop_undo(token[:4])
    assert rec is not None and rec["action"] == "pause"
