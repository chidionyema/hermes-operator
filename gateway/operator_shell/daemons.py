"""Estate daemons — live launchctl status + start/stop/restart from phone."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

# Operator-facing estate labels. Gateway start is fenced (token door).
_ESTATE_DAEMONS: Tuple[str, ...] = (
    "ai.hermes.gateway",
    "ai.hermes.coordinator",
    "ai.hermes.watchdog",
    "ai.hermes.progress",
    "ai.hermes.rsi",
    "ai.hermes.otto-server",
)

# Retired / dual-door hazards — show status, never casual start.
_RETIRED = frozenset({"ai.hermes.cockpit", "ai.hermes.ngrok"})
_FENCED_START = frozenset({"ai.hermes.gateway", "ai.hermes.cockpit", "ai.hermes.ngrok"})

_SHORT = {
    "ai.hermes.gateway": "gateway",
    "ai.hermes.coordinator": "coord",
    "ai.hermes.watchdog": "watch",
    "ai.hermes.progress": "prog",
    "ai.hermes.rsi": "rsi",
    "ai.hermes.otto-server": "otto-http",
    "ai.hermes.cockpit": "cockpit",
    "ai.hermes.ngrok": "ngrok",
}


def _uid() -> int:
    return os.getuid()  # windows-footgun: ok — POSIX launchd (macOS) helper, never invoked on Windows


def _plist_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def discover_labels() -> List[str]:
    """ai.hermes.* plists on disk + known estate set (deduped, stable order)."""
    found = []
    try:
        for p in sorted(_plist_dir().glob("ai.hermes.*.plist")):
            found.append(p.stem)
    except Exception:
        pass
    ordered: List[str] = []
    for label in list(_ESTATE_DAEMONS) + found:
        if label not in ordered:
            ordered.append(label)
    # Append retired last if present on disk
    for label in sorted(_RETIRED):
        if label in found and label not in ordered:
            ordered.append(label)
    return ordered


def launchctl_state(label: str) -> Dict[str, object]:
    """Live launchctl print — {running, pid, state, detail}."""
    target = f"gui/{_uid()}/{label}"
    try:
        r = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and "Could not find service" in out:
            return {
                "running": False,
                "pid": None,
                "state": "unloaded",
                "detail": "not loaded",
            }
        state = "unknown"
        pid: Optional[int] = None
        running = False
        for ln in out.splitlines():
            s = ln.strip()
            if s.startswith("state ="):
                state = s.split("=", 1)[1].strip()
            if s.startswith("pid ="):
                try:
                    pid = int(s.split("=", 1)[1].strip())
                except Exception:
                    pid = None
            if "runs =" in s or "last exit code" in s.lower():
                pass
        running = state == "running" or (pid is not None and pid > 0)
        # Disabled key in plist
        disabled = False
        plist = _plist_dir() / f"{label}.plist"
        if plist.is_file():
            try:
                raw = plist.read_text(encoding="utf-8", errors="replace")
                if "<key>Disabled</key>" in raw and "<true/>" in raw[
                    raw.find("<key>Disabled</key>") : raw.find("<key>Disabled</key>") + 80
                ]:
                    disabled = True
            except Exception:
                pass
        if disabled and not running:
            state = "disabled"
        return {
            "running": running,
            "pid": pid,
            "state": state,
            "detail": f"pid {pid}" if pid else state,
            "disabled": disabled,
        }
    except Exception as exc:
        logger.warning("launchctl_state %s: %s", label, exc)
        return {
            "running": False,
            "pid": None,
            "state": "error",
            "detail": str(exc)[:40],
        }


def _emoji(st: Dict[str, object]) -> str:
    if st.get("state") == "disabled":
        return "⚪"
    if st.get("state") == "unloaded":
        return "⚫"
    if st.get("running"):
        return "🟢"
    return "🔴"


def render_daemons() -> Tuple[str, List[ButtonRow]]:
    lines = ["⚙️ *Daemons* — live `launchctl`", ""]
    down: List[str] = []
    for label in discover_labels():
        st = launchctl_state(label)
        short = _SHORT.get(label, label.replace("ai.hermes.", ""))
        tag = ""
        if label in _FENCED_START:
            tag = " · fenced"
        if label in _RETIRED:
            tag = " · retired"
        lines.append(
            f"{_emoji(st)} `{short}` · {st.get('detail')}{tag}"
        )
        if not st.get("running") and label not in _RETIRED and st.get("state") != "disabled":
            down.append(short)

    if down:
        lines.append("")
        lines.append(f"⬇️ down: {', '.join(down)}")
    lines.append("")
    lines.append("_Gateway start is fenced. Coord/watch/rsi: tap below._")
    lines.append("_Prospector generation: tap Prospect daemons._")

    # Action rows — coordinator first (most common), then others (not gateway start)
    buttons: List[ButtonRow] = [
        [
            ("♻️ Restart coord", "estate:daemon_restart:coordinator"),
            ("▶️ Start coord", "estate:daemon_start:coordinator"),
        ],
        [
            ("♻️ Hermes watch", "estate:daemon_restart:watchdog"),
            ("♻️ RSI", "estate:daemon_restart:rsi"),
            ("♻️ Progress", "estate:daemon_restart:progress"),
        ],
        [
            ("⏹ Stop coord", "estate:daemon_stop:coordinator"),
            ("♻️ Bounce gateway", "estate:daemon_restart:gateway"),
        ],
        [
            ("⚙️ Prospect daemons", "estate:prospector_daemon"),
            ("🔄 Refresh", "estate:daemons"),
            ("🎛 Mission", "estate:refresh"),
        ],
    ]
    return "\n".join(lines), buttons


def _resolve_short(arg: str) -> Optional[str]:
    a = (arg or "").strip().lower().replace("ai.hermes.", "")
    if not a:
        return None
    aliases = {
        "gateway": "ai.hermes.gateway",
        "gw": "ai.hermes.gateway",
        "coord": "ai.hermes.coordinator",
        "coordinator": "ai.hermes.coordinator",
        "watch": "ai.hermes.watchdog",
        "watchdog": "ai.hermes.watchdog",
        "prog": "ai.hermes.progress",
        "progress": "ai.hermes.progress",
        "rsi": "ai.hermes.rsi",
        "otto": "ai.hermes.otto-server",
        "otto-server": "ai.hermes.otto-server",
        "otto-http": "ai.hermes.otto-server",
    }
    if a in aliases:
        return aliases[a]
    full = f"ai.hermes.{a}"
    if full in discover_labels() or full in _ESTATE_DAEMONS:
        return full
    return None


def confirm_card(op: str, label: str) -> Tuple[str, List[ButtonRow]]:
    short = _SHORT.get(label, label)
    warn = ""
    if label == "ai.hermes.gateway":
        warn = "\n\n⚠️ Drops Telegram for a few seconds."
    if label in _RETIRED:
        warn = "\n\n⚠️ Retired dual-door — only if you know why."
    op_word = {"start": "Start", "stop": "Stop", "restart": "Restart"}.get(op, op)
    text = f"⚙️ *{op_word}* `{short}`?{warn}"
    buttons: List[ButtonRow] = [
        [
            ("✅ Confirm", f"estate:daemon_{op}_confirm:{_SHORT.get(label, label.replace('ai.hermes.', ''))}"),
            ("✗ Cancel", "estate:daemons"),
        ]
    ]
    return text, buttons


def run_op(op: str, label: str) -> Tuple[bool, str]:
    """Execute start/stop/restart via launchctl. Returns (ok, detail)."""
    if op == "start" and label in _FENCED_START:
        return False, f"`{label}` start is fenced — use Bounce gateway confirm or CLI"
    target = f"gui/{_uid()}/{label}"
    plist = _plist_dir() / f"{label}.plist"
    try:
        if op == "restart":
            cmd = ["launchctl", "kickstart", "-k", target]
        elif op == "stop":
            cmd = ["launchctl", "bootout", target]
        elif op == "start":
            if not plist.is_file():
                return False, f"no plist {plist.name}"
            # bootstrap if unloaded, else kickstart
            st = launchctl_state(label)
            if st.get("state") == "unloaded":
                cmd = ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
            else:
                cmd = ["launchctl", "kickstart", target]
        else:
            return False, f"unknown op {op}"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        detail = ((r.stderr or r.stdout or "").strip() or "ok")[:200]
        ok = r.returncode == 0
        # bootout returns non-zero if already unloaded — treat as ok for stop
        if op == "stop" and r.returncode != 0 and "No such process" in detail:
            ok = True
        return ok, detail
    except Exception as exc:
        return False, str(exc)[:200]
