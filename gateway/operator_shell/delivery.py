"""One delivery policy for gateway progress, coordinator, and cron."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryPolicy:
    """Operator-facing delivery hygiene.

    escalate: always notify (sound/badge-worthy)
    progress: silent edit of one bubble (no new spam)
    cron: prefer dedicated Telegram topic when TELEGRAM_CRON_THREAD_ID set
    tool_progress_default: telegram platform override target
    """

    escalate: str = "notify"
    progress: str = "silent_edit"
    cron_topic_required: bool = False
    telegram_tool_progress: str = "new"


def _load_yaml_config() -> Dict[str, Any]:
    try:
        from gateway.run import _load_gateway_config

        return _load_gateway_config() or {}
    except Exception:
        try:
            from hermes_constants import get_hermes_home
            import yaml

            path = Path(get_hermes_home()) / "config.yaml"
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def load_delivery_policy(cfg: Optional[Dict[str, Any]] = None) -> DeliveryPolicy:
    cfg = cfg if cfg is not None else _load_yaml_config()
    block = cfg.get("operator_shell") if isinstance(cfg.get("operator_shell"), dict) else {}
    delivery = block.get("delivery") if isinstance(block.get("delivery"), dict) else {}
    return DeliveryPolicy(
        escalate=str(delivery.get("escalate", "notify")),
        progress=str(delivery.get("progress", "silent_edit")),
        cron_topic_required=bool(delivery.get("cron_topic_required", False)),
        telegram_tool_progress=str(
            delivery.get("telegram_tool_progress")
            or block.get("telegram_tool_progress")
            or "new"
        ),
    )


def cron_topic_advisory() -> str:
    """Return operator guidance when cron topic is unset."""
    import os

    if os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip():
        return ""
    policy = load_delivery_policy()
    if not policy.cron_topic_required:
        return (
            "\n\n_Tip: set `TELEGRAM_CRON_THREAD_ID` to a Telegram topic so cron "
            "doesn't land in an unrepliable lobby. `/sethome` in a topic also helps._"
        )
    return (
        "\n\n⚠️ `TELEGRAM_CRON_THREAD_ID` unset — cron may land in the root lobby. "
        "Create a Cron topic and set the env var."
    )


def cycle_telegram_tool_progress() -> str:
    """Cycle Telegram tool_progress: off → new → all → verbose → off. Persist to config."""
    from utils import atomic_yaml_write
    from hermes_constants import get_hermes_home
    import yaml

    config_path = Path(get_hermes_home()) / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    if not isinstance(user_config, dict):
        user_config = {}

    display = user_config.setdefault("display", {})
    if not isinstance(display, dict):
        display = {}
        user_config["display"] = display
    platforms = display.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
        display["platforms"] = platforms
    tg = platforms.setdefault("telegram", {})
    if not isinstance(tg, dict):
        tg = {}
        platforms["telegram"] = tg

    cycle = ["off", "new", "all", "verbose"]
    current = str(tg.get("tool_progress") or "off")
    if current not in cycle:
        current = "off"
    new_mode = cycle[(cycle.index(current) + 1) % len(cycle)]
    tg["tool_progress"] = new_mode
    atomic_yaml_write(config_path, user_config)
    return new_mode
