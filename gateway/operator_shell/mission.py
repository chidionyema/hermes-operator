"""Pinned mission card — Elon cockpit: one glance, one CTA, RSI visible.

Honesty rule: never 🟢 CLEAR when anything is blocked/degraded/busy.
"""

from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


def _coord():
    from gateway.operator_shell.estate import _load_coordinator

    return _load_coordinator()


def _cb_bits(C) -> Tuple[bool, bool, str]:
    """Return (claude_ok, agy_ok, detail). True = healthy."""
    claude_ok = agy_ok = True
    try:
        if hasattr(C, "_circuit_breaker_status"):
            claude_ok = bool(C._circuit_breaker_status("claude"))
            agy_ok = bool(C._circuit_breaker_status("agy"))
    except Exception:
        pass
    if claude_ok and agy_ok:
        return True, True, ""
    if not claude_ok and not agy_ok:
        return False, False, "Claude+agy CB open"
    if not claude_ok:
        return False, True, "Claude CB open"
    return True, False, "agy CB open"


def _blocked_missions(conn) -> int:
    try:
        import flight

        return sum(1 for m in flight.list_missions(conn) if m["status"] == "blocked")
    except Exception:
        return 0


def _inflight_code(conn) -> Optional[Tuple[str, str]]:
    try:
        row = conn.execute(
            "SELECT id, status FROM tasks WHERE source='code:telegram' "
            "AND status IN ('open','diagnosed','executing','verifying') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        tid = row["id"] if hasattr(row, "keys") else row[0]
        st = row["status"] if hasattr(row, "keys") else row[1]
        return str(tid), str(st)
    except Exception:
        return None


def _verdict(conn, C) -> Tuple[str, str]:
    """Return (emoji_word, detail). Never false-CLEAR when estate needs attention."""
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
        blocked_n = _blocked_missions(conn)
        code = _inflight_code(conn)
        claude_ok, agy_ok, cb_detail = _cb_bits(C)

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
        if not claude_ok and not agy_ok:
            return "🔴 CB", cb_detail
        if dec:
            return "🟡 BLOCKED", f"{len(dec)} need you"
        if blocked_n:
            return "🟡 BLOCKED", f"{blocked_n} mission(s) blocked"
        if code:
            tid, st = code
            return "🟡 BUSY", f"code `{tid[:8]}` {st}"
        if not claude_ok:
            return "🟡 DEGRADED", cb_detail
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
        claude_ok, agy_ok, cb_detail = _cb_bits(C)
        if not claude_ok or not agy_ok:
            return f"CB {cb_detail}"
        code = _inflight_code(conn)
        if code:
            return f"CODE `{code[0][:8]}` {code[1]}"
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
    """Exactly one primary CTA — real next estate action, not decoration."""
    if C.estate_paused():
        return ("▶️ Resume spend", "estate:resume")
    # Daemon / gateway down → restart path (not Prospector)
    try:
        gateway_ok = (
            C.gateway_alive() if hasattr(C, "gateway_alive") else True
        )
        hb = C.get_meta(conn, "last_tick")
        tick_age = int(time.time() - hb["updated_at"]) if hb else None
        daemon_ok = tick_age is not None and tick_age < 200
        daemon_proc = C._proc_alive("coordinator.py daemon")
        if hasattr(C, "_launchctl_running"):
            daemon_proc = daemon_proc or (
                C._launchctl_running("ai.hermes.coordinator") is True
            )
        if not (daemon_ok or daemon_proc):
            return ("♻️ Restart coord", "estate:restart")
        if not gateway_ok:
            return ("⚙️ Daemons", "estate:daemons")
    except Exception:
        pass

    # Money/identity fence always wins — code fences deep-link to task card
    try:
        fence = conn.execute(
            "SELECT id, source FROM tasks WHERE status='awaiting_approval' "
            "AND risk_class IN ('money','identity','contract') "
            "ORDER BY CASE WHEN source='code:telegram' THEN 0 ELSE 1 END, created_at DESC "
            "LIMIT 1"
        ).fetchone()
        if fence:
            fid = fence["id"] if hasattr(fence, "keys") else fence[0]
            src = fence["source"] if hasattr(fence, "keys") else (
                fence[1] if len(fence) > 1 else ""
            )
            if src == "code:telegram":
                return (f"💰 Code fence {str(fid)[:8]}", f"estate:task:{str(fid)[:8]}")
            return ("💰 Approve fence", "estate:inbox")
    except Exception:
        pass

    # Dual CB → fuel/honesty, not fake ship
    claude_ok, agy_ok, _ = _cb_bits(C)
    if not claude_ok and not agy_ok:
        return ("⛽ Fuel / CB", "estate:system_fuel")

    # In-flight coding run
    code = _inflight_code(conn)
    if code:
        tid, st = code
        label = {
            "executing": "💻 Code run",
            "verifying": "🔎 Code verify",
        }.get(st, "💻 Code run")
        return (f"{label} {tid[:8]}", f"estate:task:{tid[:8]}")

    try:
        dec = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        if dec:
            return ("📥 Decide", "estate:inbox")
    except Exception:
        pass
    if _blocked_missions(conn):
        return ("📥 Decide", "estate:inbox")

    if "BUDGET" in verdict:
        return ("⛽ Fuel", "estate:system_fuel")
    if "DEGRADED" in verdict or "CB" in verdict:
        return ("⚙️ Daemons", "estate:daemons")

    # RSI live fire → surface before busywork
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

    # Only when truly CLEAR — fleet overview beats Prospector tunnel vision
    return ("🚀 Fleet", "estate:fleet")


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
            ("⚙️ Daemons", "estate:daemons"),
        ],
        [
            ("🔄 Refresh", "estate:refresh"),
            ("⛽ Fuel", "estate:system_fuel"),
            ("🗓 Cron", "estate:setup_cron_topic"),
        ],
    ]


def render_mission_card() -> Tuple[str, bool, List[ButtonRow]]:
    """Compact forever-card — brand-dense, zero theater, honest verdict."""
    C = _coord()
    conn = C.connect()
    try:
        verdict, detail = _verdict(conn, C)
        burn = _burn_today(conn, C)
        blocker = _top_blocker(conn, C)
        prod = _product_autonomy(conn, C)
        product = _product_line(conn, C)
        paused = bool(C.estate_paused())
        primary = _primary_cta(conn, C, f"{verdict} — {detail}")
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
