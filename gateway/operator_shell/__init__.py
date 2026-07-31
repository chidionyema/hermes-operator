"""Telegram-first operator shell — mission card, inbox, fleet, proof, natural ops."""

import logging as _logging

from gateway.operator_shell import integrity as _integrity
from gateway.operator_shell.cron_ops import format_cron_command
from gateway.operator_shell.integrity import ghost_modules
from gateway.operator_shell.delivery import DeliveryPolicy, load_delivery_policy
from gateway.operator_shell.estate import (
    PanelView,
    handle_estate_action,
    render_panel_view,
)
from gateway.operator_shell.menu import (
    OPERATOR_TELEGRAM_MENU,
    resolve_telegram_menu_profile,
)

__all__ = [
    "DeliveryPolicy",
    "OPERATOR_TELEGRAM_MENU",
    "PanelView",
    "format_cron_command",
    "ghost_modules",
    "handle_estate_action",
    "load_delivery_policy",
    "render_panel_view",
    "resolve_telegram_menu_profile",
]

# Say out loud, in the gateway's own log, when this package contains modules git
# has never seen (see integrity.py for the incident this exists for). WARN by
# default; HERMES_STRICT_TRACKED_IMPORTS=1 makes it fatal.
#
# Guarded: an integrity check that crash-loops the gateway would be a worse bug
# than the one it detects. Strict mode is re-raised deliberately.
try:
    _integrity.enforce()
except RuntimeError:
    raise
except Exception as _exc:  # pragma: no cover - defensive
    _logging.getLogger(__name__).warning("integrity check unavailable: %s", _exc)
