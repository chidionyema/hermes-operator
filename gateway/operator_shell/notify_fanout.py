"""Dual-path P0 notify — Telegram + optional ntfy when Telegram blips."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def _ntfy_topic() -> Optional[str]:
    topic = (os.getenv("NTFY_TOPIC") or os.getenv("HERMES_NTFY_TOPIC") or "").strip()
    if topic:
        return topic
    try:
        import yaml
        from pathlib import Path
        cfg = yaml.safe_load(
            Path(os.path.expanduser("~/.hermes/config.yaml")).read_text()
        ) or {}
        block = cfg.get("operator_shell") or {}
        return str(block.get("ntfy_topic") or "").strip() or None
    except Exception:
        return None


def fanout_p0(msg: str) -> bool:
    """Best-effort: Telegram first, then ntfy for P0. Returns True if any path worked."""
    ok = False
    try:
        r = subprocess.run(
            ["hermes", "send", "--to", "telegram", msg[:3500]],
            timeout=45,
            capture_output=True,
        )
        ok = r.returncode == 0
    except Exception as exc:
        logger.warning("telegram fanout failed: %s", exc)

    topic = _ntfy_topic()
    if topic and (not ok or os.getenv("OPERATOR_SHELL_ALWAYS_NTFY") == "1"):
        try:
            # Prefer hermes send if ntfy platform configured
            r2 = subprocess.run(
                ["hermes", "send", "--to", f"ntfy:{topic}", msg[:3500]],
                timeout=30,
                capture_output=True,
            )
            if r2.returncode == 0:
                ok = True
            else:
                # raw ntfy curl fallback
                import urllib.request

                req = urllib.request.Request(
                    f"https://ntfy.sh/{topic}",
                    data=msg[:3500].encode("utf-8"),
                    method="POST",
                    headers={"Title": "Hermes P0", "Priority": "high"},
                )
                urllib.request.urlopen(req, timeout=15)
                ok = True
        except Exception as exc:
            logger.warning("ntfy fanout failed: %s", exc)
    return ok


def patch_coordinator_notifier() -> None:
    """Optional: wrap coordinator.telegram_notify for escalate path. Call once at gateway boot."""
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        if getattr(C, "_operator_shell_patched", False):
            return
        original = C.telegram_notify

        def wrapped(msg: str) -> bool:
            ok = original(msg)
            # On failure, try ntfy for P0-looking messages
            if not ok or any(
                k in (msg or "").lower()
                for k in ("escalat", "budget", "degraded", "gateway", "auth", "money")
            ):
                if not ok:
                    fanout_p0(msg)
            return ok

        C.telegram_notify = wrapped  # type: ignore
        C._operator_shell_patched = True
    except Exception as exc:
        logger.debug("coordinator notifier patch skipped: %s", exc)
