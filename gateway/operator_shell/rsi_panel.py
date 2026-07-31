"""RSI / self-improvement cockpit — phone-native, ≤2 taps from /panel."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

HERMES = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))
OFF_SWITCH = HERMES / "meta" / "OFF_SWITCH"
PENDING_DIR = HERMES / "meta" / "pending"
PENDING_CHANGES = HERMES / "meta" / "pending-changes.json"
PROOFS_DIR = HERMES / "meta" / "proofs"
HASH_FILE = HERMES / "meta" / "reference-script-hash.json"
IDLE_LOG = HERMES / "logs" / "maintenance" / "idle-learning-runs.jsonl"


def _ago(ts: float) -> str:
    age = max(0, int(time.time() - ts))
    if age < 90:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    if age < 86400:
        return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


def _parse_iso(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        s = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def learning_armed() -> bool:
    return OFF_SWITCH.is_file()


def glance_line() -> str:
    """One-line RSI for mission card / brief."""
    armed = learning_armed()
    staged = _staged_count()
    idle = _last_idle()
    idle_bit = "—"
    if idle:
        if not _idle_is_live_fire(idle) and (
            idle.get("exit") != 0 or idle.get("failed_phases")
        ):
            idle_bit = f"cleared {idle.get('_ago') or '?'}"
        else:
            ok = idle.get("exit") == 0 and not idle.get("failed_phases")
            mark = "ok" if ok else "fail"
            when = idle.get("_ago") or "?"
            idle_bit = f"{mark} {when}"
    return (
        f"🧠 RSI `{'ARMED' if armed else 'OFF'}` · idle `{idle_bit}` · "
        f"staged `{staged}` · proofs `{_proof_count()}`"
    )


def _staged_count() -> int:
    n = 0
    if PENDING_DIR.is_dir():
        n += len(list(PENDING_DIR.glob("pending_*.json")))
    if PENDING_CHANGES.is_file():
        try:
            data = json.loads(PENDING_CHANGES.read_text())
            if isinstance(data, list):
                n += len(data)
            elif isinstance(data, dict):
                n += len(data.get("changes") or data.get("pending") or [])
        except Exception:
            pass
    return n


def _proof_count() -> int:
    if not PROOFS_DIR.is_dir():
        return 0
    return len(list(PROOFS_DIR.glob("*.json")))


def _last_idle() -> Optional[dict]:
    if not IDLE_LOG.is_file():
        return None
    try:
        lines = IDLE_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if not lines:
            return None
        row = json.loads(lines[-1])
        ts = _parse_iso(row.get("ts") or "")
        if ts:
            row["_ago"] = _ago(ts)
            row["_ts"] = ts
        return row
    except Exception:
        return None


def _hash_ok() -> Tuple[str, str]:
    """Return (emoji_word, detail) for Phase0 bootstrap hash."""
    if not HASH_FILE.is_file():
        return "🔴", "no bootstrap hash — run meta-improver --bootstrap-hash"
    try:
        age = _ago(HASH_FILE.stat().st_mtime)
        return "🟢", f"hash set · {age}"
    except Exception as exc:
        return "🟡", str(exc)[:40]


def _evidence_glance() -> str:
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            rows = conn.execute(
                "SELECT verifier_verdict FROM evidence ORDER BY ts DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return "0 verified"
        pass_n = sum(1 for r in rows if (r["verifier_verdict"] if hasattr(r, "keys") else r[0]) == "PASS")
        return f"{pass_n}/{len(rows)} PASS (recent)"
    except Exception:
        return "n/a"


def _idle_is_live_fire(idle: Optional[dict]) -> bool:
    """True when last idle failure is still actionable (not cleared by newer hash)."""
    if not idle:
        return False
    if idle.get("exit") == 0 and not idle.get("failed_phases"):
        return False
    phases = str(idle.get("failed_phases") or "")
    idle_ts = float(idle.get("_ts") or 0)
    hash_m = HASH_FILE.stat().st_mtime if HASH_FILE.is_file() else 0.0
    if "Phase 0" in phases and hash_m and idle_ts and hash_m > idle_ts:
        return False
    return bool(idle.get("exit") != 0 or idle.get("failed_phases"))


def _primary_next(armed: bool, idle: Optional[dict], staged: int) -> Tuple[str, str]:
    """Single next action for the RSI surface."""
    if not armed:
        return ("🟢 Arm learning", "estate:arm_learning")
    if _idle_is_live_fire(idle):
        return ("🔄 Refresh status", "estate:rsi")
    if staged > 0:
        return ("📥 Open inbox", "estate:inbox")
    return ("🎛 Mission", "estate:refresh")


def render_rsi_panel() -> Tuple[str, List[ButtonRow]]:
    """Dense founder surface — no essays."""
    armed = learning_armed()
    idle = _last_idle()
    staged = _staged_count()
    proofs = _proof_count()
    hash_emoji, hash_detail = _hash_ok()
    evidence = _evidence_glance()
    primary = _primary_next(armed, idle, staged)

    arm_word = "🟢 ARMED" if armed else "⚪ OFF"
    # Stale Phase0 fail after a newer bootstrap-hash shouldn't look like a live fire.
    hash_mtime = HASH_FILE.stat().st_mtime if HASH_FILE.is_file() else 0.0
    idle_stale_phase0 = False
    if idle:
        phases = idle.get("failed_phases") or "—"
        reason = idle.get("reason") or "?"
        exit_c = idle.get("exit")
        idle_ts = float(idle.get("_ts") or 0)
        phase0_only = "Phase 0" in str(phases) and "Phase 1" not in str(phases)
        if (
            exit_c != 0
            and phase0_only
            and hash_mtime
            and idle_ts
            and hash_mtime > idle_ts
        ):
            idle_stale_phase0 = True
            idle_line = (
                f"🟡 `{idle.get('_ago', '?')}` Phase0 fail *before* bootstrap — "
                f"hash now green; next idle run should clear"
            )
        else:
            idle_line = (
                f"{'🟢' if exit_c == 0 and phases in ('', '—', None) else '🔴'} "
                f"`{idle.get('_ago', '?')}` · exit `{exit_c}` · {reason}"
            )
            if phases and phases not in ("—", ""):
                idle_line += f"\n   failed: `{phases}`"
    else:
        idle_line = "—"

    # Don't CTA-trap on a cleared Phase0 after bootstrap.
    if idle_stale_phase0:
        primary = ("🎛 Mission", "estate:refresh")

    text = "\n".join(
        [
            "🧠 *Self-improvement*",
            "",
            f"*{arm_word}* — OFF_SWITCH {'present' if armed else 'absent'}",
            f"Idle-learning: {idle_line}",
            f"Phase0 / hash: {hash_emoji} {hash_detail}",
            f"Staged changes: `{staged}` · proofs: `{proofs}`",
            f"Evidence ledger: `{evidence}`",
            "",
            f"→ *{primary[0]}*",
        ]
    )

    arm_btn = (
        ("⛔ Disarm", "estate:disarm_learning")
        if armed
        else ("🟢 Arm", "estate:arm_learning")
    )
    buttons: List[ButtonRow] = [
        [primary],
        [arm_btn, ("🔄 Refresh", "estate:rsi")],
        [
            ("📥 Inbox", "estate:inbox"),
            ("🎛 Mission", "estate:refresh"),
            ("⛽ Fuel", "estate:system_fuel"),
        ],
    ]
    return text, buttons
