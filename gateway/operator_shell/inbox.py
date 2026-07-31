"""Decision inbox — phone-native: money fences first, unmistakable APPROVE."""

from __future__ import annotations

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


def render_inbox() -> Tuple[str, List[ButtonRow]]:
    from gateway.operator_shell.estate import _load_coordinator

    C = _load_coordinator()
    conn = C.connect()
    try:
        rows = [d for d in C.decisions_view(conn) if C._is_operator_facing(d)]
        # Also surface money/identity awaiting_approval even if ranking quirks
        try:
            extra = conn.execute(
                "SELECT * FROM tasks WHERE status='awaiting_approval' "
                "AND risk_class IN ('money','identity','contract') "
                "ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
            seen = {r["id"] for r in rows}
            for e in extra:
                if e["id"] not in seen:
                    rows.append(e)
        except Exception:
            pass
        # Blocked missions (quota / escalate) — one line each
        blocked_missions = []
        try:
            import flight

            for m in flight.list_missions(conn):
                if m["status"] == "blocked":
                    blocked_missions.append(m)
        except Exception:
            pass
    finally:
        conn.close()

    buttons: List[ButtonRow] = []
    lines: List[str] = []

    if not rows and not blocked_missions:
        return (
            "📥 *Inbox* — clear\n\nNothing needs you.",
            [
                [
                    ("🎛 Mission", "estate:refresh"),
                    ("🧠 RSI", "estate:rsi"),
                    ("🚀 Fleet", "estate:fleet"),
                ]
            ],
        )

    money = [d for d in rows if (d["risk_class"] or "").lower() in ("money", "identity", "contract")
             and d["status"] == "awaiting_approval"]
    other = [d for d in rows if d not in money]

    if money:
        lines.append(f"💰 *MONEY/IDENTITY FENCE* — `{len(money)}` need APPROVE")
        lines.append("_No auto-run. Tap ✅ only when you mean it._")
        lines.append("")
        for d in money[:6]:
            short = d["id"][:8]
            risk = (d["risk_class"] or "").upper()
            lines.append(f"⏸ `{short}` [{risk}] {d['title'][:40]}")
            buttons.append(
                [
                    (f"✅ APPROVE {short}", f"estate:approve:{short}"),
                    (f"👁 {short}", f"estate:inspect:{short}"),
                ]
            )
        lines.append("")

    if other:
        lines.append(f"📥 *Also* — `{len(other)}`")
        for d in other[:6]:
            short = d["id"][:8]
            tag = "⏸ APPROVE" if d["status"] == "awaiting_approval" else "🔴 BLOCKED"
            lines.append(f"{tag} `{short}` {d['title'][:44]}")
            if d["status"] == "awaiting_approval":
                buttons.append(
                    [
                        (f"✅ {short}", f"estate:approve:{short}"),
                        (f"👁 {short}", f"estate:inspect:{short}"),
                    ]
                )
            else:
                buttons.append([(f"👁 {short}", f"estate:inspect:{short}")])
        lines.append("")

    if blocked_missions:
        lines.append(f"🚀 *Missions blocked* — `{len(blocked_missions)}`")
        for m in blocked_missions[:3]:
            lines.append(f"🔴 `{m['id'][:8]}` {m['name'][:40]}")
        lines.append("_Usually Claude quota — see mission card blocker._")
        lines.append("")

    buttons.append(
        [
            ("🎛 Mission", "estate:refresh"),
            ("🧠 RSI", "estate:rsi"),
            ("🚀 Fleet", "estate:fleet"),
        ]
    )
    return "\n".join(lines).strip(), buttons
