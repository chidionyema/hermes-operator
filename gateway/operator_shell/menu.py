"""Operator Telegram Bot menu profile (≤12 commands)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

# Tier-0 operator shell — must fit Telegram's practical menu and stay typed-first.
# Keep in sync with ~/.hermes/scripts/set-cockpit-menu.py (chat-scoped wins).
OPERATOR_TELEGRAM_MENU: Tuple[str, ...] = (
    "panel",
    "inbox",
    "fleet",
    "brief",
    "cron",
    "busy",
    "notify",
    "revert",
    "missions",
    "audit",
    "help",
    "sethome",
)


def resolve_telegram_menu_profile(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Return ``operator`` | ``default`` from config."""
    if cfg is None:
        try:
            from gateway.run import _load_gateway_config

            cfg = _load_gateway_config() or {}
        except Exception:
            cfg = {}
    block = cfg.get("operator_shell") if isinstance(cfg.get("operator_shell"), dict) else {}
    profile = str(block.get("menu_profile") or "").strip().lower()
    if profile in {"operator", "default", "full"}:
        return "operator" if profile == "operator" else profile
    # Also allow telegram.menu_profile
    tg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    profile = str(tg.get("menu_profile") or "").strip().lower()
    if profile in {"operator", "default", "full"}:
        return profile
    return "default"


def filter_operator_menu(
    commands: Sequence[Tuple[str, str]],
) -> list[Tuple[str, str]]:
    """Keep only Tier-0 commands, in OPERATOR_TELEGRAM_MENU order."""
    by_name = {name: desc for name, desc in commands}
    out: list[Tuple[str, str]] = []
    for name in OPERATOR_TELEGRAM_MENU:
        if name in by_name:
            out.append((name, by_name[name]))
    return out
