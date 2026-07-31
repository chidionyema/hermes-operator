"""Pinned mission card — Elon cockpit: one glance, one CTA, RSI visible."""

from __future__ import annotations

import logging
import os
import time
from typing import List, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


def _coord():
    from gateway.operator_shell.estate import _load_coordinator

    return _load_coordinator()


def _verdict(conn, C) -> Tuple[str, str]:
    """Return (emoji_word, detail). Never false-DEGRADED via fragile pgrep."""
    try:
        hb = C.get_meta(conn, "last_tick")
        tick_age = int(time.time() - hb["updated_at"]) if hb else None
        daemon_ok = tick_age is not None and tick_age < 200
        gateway_ok = (
            C.gateway_alive() if hasattr(C, "gateway_alive") else C._proc_alive("gateway run")
        )
        daemon_proc = C._proc_alive("coordinator.py daemon")
        if hasattr(C, "_launchctl_running"):
            daemon_proc = daemon_proc or (
                C._launchctl_running("ai.hermes.coordinator") is True
            )
        paused = C.estate_paused()
        used = C.tasks_today(conn)
        budget = C.DAILY_TASK_BUDGET
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if paused:
            return "🟡 PAUSED", "spend frozen"
        if not ((daemon_ok or daemon_proc) and gateway_ok):
            bits = []
            if not (daemon_ok or daemon_proc):
                bits.append(
                    f"daemon {tick_age}s" if tick_age is not None else "daemon down"
                )
            if not gateway_ok:
                bits.append("gateway down")
            return "🔴 DEGRADED", " · ".join(bits)
        if used >= budget:
            return "🔴 BUDGET", f"{used}/{budget} tasks"
        if dec:
            return "🟡 BLOCKED", f"{len(dec)} need you"
        return "🟢 CLEAR", "go"
    except Exception as exc:
        logger.warning("verdict failed: %s", exc)
        return "🔴 UNKNOWN", str(exc)[:60]


def _burn_today(conn, C) -> str:
    try:
        m = C.autonomy_ratio(conn, 86400)
        cost = float(m.get("total_cost", 0.0) or 0.0)
        used = C.tasks_today(conn)
        return f"${cost:.2f} · {used}/{C.DAILY_TASK_BUDGET}"
    except Exception:
        return "n/a"


def _top_blocker(conn, C) -> str:
    try:
        # Money/identity fences first — never bury under housekeeping
        try:
            fences = conn.execute(
                "SELECT id,title,risk_class FROM tasks WHERE status='awaiting_approval' "
                "AND risk_class IN ('money','identity','contract') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if fences:
                return (
                    f"APPROVE [{(fences['risk_class'] or '').upper()}] "
                    f"`{fences['id'][:8]}` {fences['title'][:32]}"
                )
        except Exception:
            pass
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            d = dec[-1]
            tag = "APPROVE" if d["status"] == "awaiting_approval" else "BLOCKED"
            risk = (d["risk_class"] or "").upper()
            risk_bit = f" [{risk}]" if risk in ("MONEY", "IDENTITY") else ""
            return f"{tag}{risk_bit} `{d['id'][:8]}` {d['title'][:36]}"
        # Blocked product missions (often quota) — surface on card
        try:
            import flight

            for m in flight.list_missions(conn):
                if m["status"] == "blocked":
                    return f"MISSION `{m['id'][:8]}` {m['name'][:28]} blocked (quota?)"
        except Exception:
            pass
        return "—"
    except Exception:
        return "—"


def _product_line(conn, C) -> str:
    """One line: active product mission + acceptance/blocker."""
    try:
        import flight

        for m in flight.list_missions(conn):
            if m["status"] not in ("flying", "blocked", "plotting"):
                continue
            cur = next(
                (x for x in flight.milestones(conn, m["id"]) if x["status"] != "done"),
                None,
            )
            if not cur:
                continue
            st = m["status"].upper()
            acc = (cur["done_criterion"] or "")[:48]
            return f"🚀 `{m['name'][:18]}` {st} · M{cur['seq']+1}: {cur['title'][:28]}"
    except Exception:
        pass
    return ""


def _product_autonomy(conn, C) -> str:
    try:
        m = C.autonomy_ratio(conn, 7 * 86400)
        return (
            f"`{m.get('product_autonomy_ratio', 0)*100:.0f}%` · "
            f"{m.get('product_auto_resolved', 0)} done / "
            f"{m.get('product_escalated', 0)} ask"
        )
    except Exception:
        return "n/a"


def _primary_cta(conn, C, verdict: str) -> Tuple[str, str]:
    """Exactly one primary CTA — founder action, not decoration."""
    if C.estate_paused():
        return ("▶️ Resume spend", "estate:resume")
    # Money/identity fence always wins — but code:telegram fences deep-link to the task card
    try:
        fence = conn.execute(
            "SELECT id, source FROM tasks WHERE status='awaiting_approval' "
            "AND risk_class IN ('money','identity','contract') "
            "ORDER BY CASE WHEN source='code:telegram' THEN 0 ELSE 1 END, created_at DESC "
            "LIMIT 1"
        ).fetchone()
        if fence:
            fid = fence["id"] if hasattr(fence, "keys") else fence[0]
            src = fence["source"] if hasattr(fence, "keys") else (fence[1] if len(fence) > 1 else "")
            if src == "code:telegram":
                return (f"💰 Code fence {str(fid)[:8]}", f"estate:task:{str(fid)[:8]}")
            return ("💰 Approve fence", "estate:inbox")
    except Exception:
        pass
    # In-flight Claude Code remote → primary CTA (steer/cancel without remembering IDs)
    try:
        code_run = conn.execute(
            "SELECT id, status FROM tasks WHERE source='code:telegram' "
            "AND status IN ('open','diagnosed','executing','verifying') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if code_run:
            tid = code_run["id"] if hasattr(code_run, "keys") else code_run[0]
            st = code_run["status"] if hasattr(code_run, "keys") else code_run[1]
            label = {
                "executing": "💻 Code run",
                "verifying": "🔎 Code verify",
            }.get(st, "💻 Code run")
            return (f"{label} {str(tid)[:8]}", f"estate:task:{str(tid)[:8]}")
    except Exception:
        pass
    try:
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            return ("📥 Decide", "estate:inbox")
    except Exception:
        pass
    try:
        import flight

        if any(m["status"] == "blocked" for m in flight.list_missions(conn)):
            return ("📥 Decide", "estate:inbox")
    except Exception:
        pass
    if "DEGRADED" in verdict or "BUDGET" in verdict:
        return ("🔄 Refresh", "estate:refresh")
    # RSI live fire → surface before busywork (ignore Phase0 fails cleared by newer hash)
    try:
        from gateway.operator_shell.rsi_panel import (
            HASH_FILE,
            _last_idle,
            learning_armed,
        )

        idle = _last_idle()
        if learning_armed() and idle and (
            idle.get("exit") != 0 or idle.get("failed_phases")
        ):
            phases = str(idle.get("failed_phases") or "")
            idle_ts = float(idle.get("_ts") or 0)
            hash_m = HASH_FILE.stat().st_mtime if HASH_FILE.is_file() else 0.0
            cleared = (
                "Phase 0" in phases
                and hash_m
                and idle_ts
                and hash_m > idle_ts
            )
            if not cleared:
                return ("🧠 RSI status", "estate:rsi")
    except Exception:
        pass
    return ("⚡️ Prospector", "estate:run_prospector")


def mission_buttons(paused: bool, primary: Tuple[str, str]) -> List[ButtonRow]:
    pause_or_resume = (
        ("▶️ Resume", "estate:resume") if paused else ("⏸ Pause", "estate:pause")
    )
    return [
        [primary],
        [
            ("🧠 RSI", "estate:rsi"),
            ("🏗 CI", "estate:builds"),
            ("📥 Inbox", "estate:inbox"),
        ],
        [
            pause_or_resume,
            ("🚀 Fleet", "estate:fleet"),
            ("⛽ Fuel", "estate:system_fuel"),
        ],
        [
            ("🔄 Refresh", "estate:refresh"),
            ("↩ Undo", "estate:undo"),
            ("🗓 Cron", "estate:setup_cron_topic"),
        ],
    ]


def render_mission_card() -> Tuple[str, bool, List[ButtonRow]]:
    """Compact forever-card — brand-dense, zero theater."""
    C = _coord()
    conn = C.connect()
    try:
        verdict, detail = _verdict(conn, C)
        burn = _burn_today(conn, C)
        blocker = _top_blocker(conn, C)
        prod = _product_autonomy(conn, C)
        product = _product_line(conn, C)
        paused = bool(C.estate_paused())
        primary = _primary_cta(conn, C, verdict)
    finally:
        conn.close()

    try:
        from gateway.operator_shell.rsi_panel import glance_line

        rsi_line = glance_line()
    except Exception:
        armed = os.path.isfile(
            os.path.expanduser("~/.hermes/meta/OFF_SWITCH")
        )
        rsi_line = f"🧠 RSI `{'ARMED' if armed else 'OFF'}`"

    cron_topic = (
        "ok" if os.getenv("TELEGRAM_CRON_THREAD_ID", "").strip() else "unset · tap 🗓"
    )

    lines = [
        f"*{verdict}* — {detail}",
        f"💰 `{burn}`  ·  📈 {prod}",
        rsi_line,
        f"🧱 {blocker}",
    ]
    if product:
        lines.append(product)
    lines.extend(
        [
            f"🧵 cron `{cron_topic}`",
            "",
            f"→ *{primary[0]}*",
        ]
    )
    text = "\n".join(lines)
    return text, paused, mission_buttons(paused, primary)
