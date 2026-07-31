"""Daily $ / task hard-stop with Telegram tripwire."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _state_path() -> Path:
    d = _hermes_home() / "meta" / "operator_shell"
    d.mkdir(parents=True, exist_ok=True)
    return d / "budget_state.json"


def load_budget_config() -> Dict[str, Any]:
    try:
        from gateway.operator_shell.delivery import _load_yaml_config

        cfg = _load_yaml_config()
        block = cfg.get("operator_shell") if isinstance(cfg.get("operator_shell"), dict) else {}
        bud = block.get("budget") if isinstance(block.get("budget"), dict) else {}
    except Exception:
        bud = {}
    return {
        "max_tasks_per_day": int(bud.get("max_tasks_per_day") or os.getenv("COORD_DAILY_TASKS") or 80),
        "max_usd_per_day": float(bud.get("max_usd_per_day") or 25.0),
        "auto_pause": bool(bud.get("auto_pause", True)),
    }


def check_budget() -> Tuple[bool, str, Dict[str, Any]]:
    """Return (ok, message, metrics). ok=False means hard-stop should engage."""
    cfg = load_budget_config()
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            used = int(C.tasks_today(conn))
            m = C.autonomy_ratio(conn, 86400)
            cost = float(m.get("total_cost", 0.0) or 0.0)
        finally:
            conn.close()
    except Exception as exc:
        return True, f"budget check skipped: {exc}", {}

    metrics = {"tasks": used, "cost_usd": cost, **cfg}
    if used >= cfg["max_tasks_per_day"]:
        return False, f"Task ceiling hit ({used}/{cfg['max_tasks_per_day']})", metrics
    if cost >= cfg["max_usd_per_day"]:
        return False, f"$ ceiling hit (${cost:.2f}/${cfg['max_usd_per_day']:.2f})", metrics
    return True, "within budget", metrics


def maybe_auto_pause() -> Optional[str]:
    """If over budget and auto_pause, freeze estate. Returns notice or None."""
    ok, msg, metrics = check_budget()
    if ok:
        return None
    cfg = load_budget_config()
    if not cfg.get("auto_pause"):
        return f"⚠️ Budget: {msg} (auto-pause off)"

    state_path = _state_path()
    try:
        prev = json.loads(state_path.read_text()) if state_path.is_file() else {}
    except Exception:
        prev = {}
    # trip once per day
    day = time.strftime("%Y-%m-%d")
    if prev.get("tripped_day") == day:
        return None

    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        C.set_estate_paused(True)
    except Exception as exc:
        return f"⚠️ Budget trip failed to pause: {exc}"

    state_path.write_text(json.dumps({"tripped_day": day, "msg": msg, "metrics": metrics, "ts": time.time()}))

    notice = (
        f"🛑 *BUDGET HARD-STOP*\n{msg}\n"
        f"Estate paused. Override: `/panel` → Resume (confirm) or `estate:budget_override`."
    )
    try:
        from gateway.operator_shell.notify_fanout import fanout_p0

        fanout_p0(notice)
    except Exception as exc:
        logger.warning("budget fanout failed: %s", exc)
    return notice
