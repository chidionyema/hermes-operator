"""Builds / deploys / CI — CEO-dense ship status for phone.

Shows GitHub Actions (via `gh` when authed), local verify freshness, and
deploy signals when wired. Never invents green.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_CODE = Path.home() / "Documents" / "code"
_HERMES = Path(os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes")))

# key → (label, repo path, risk, fly app or None)
_PRODUCTS = (
    ("prospector", "Prospector", _CODE / "prospector", "low", None),
    ("signalengine", "Signal", _CODE / "signalengine", "money", None),
    ("tie", "TIE", _CODE / "the-introduction-exchange", "identity", "tie-api"),
    ("haworks-platform", "Haworks", _CODE / "haworks-platform", "low", None),
)


def _ago(ts: float) -> str:
    age = max(0, int(time.time() - ts))
    if age < 90:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


def _parse_gh_time(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _gh_auth_ok() -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "Logged in" in out:
            return True, "ok"
        if "invalid" in out.lower() or "Failed to log in" in out:
            return False, "token invalid — run `gh auth login`"
        if r.returncode != 0:
            return False, "not logged in — run `gh auth login`"
        return True, "ok"
    except FileNotFoundError:
        return False, "`gh` missing"
    except Exception as exc:
        return False, str(exc)[:40]


def _gh_latest_run(repo: Path) -> Dict[str, Any]:
    """Return {ok, status, conclusion, name, age, url, error}."""
    if not repo.is_dir():
        return {"ok": False, "error": "repo missing"}
    try:
        r = subprocess.run(
            [
                "gh", "run", "list", "--limit", "1",
                "--json", "status,conclusion,name,createdAt,url,displayTitle,headBranch",
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "gh failed")[:80]
            return {"ok": False, "error": err.strip()}
        rows = json.loads(r.stdout or "[]")
        if not rows:
            return {"ok": True, "status": "none", "conclusion": None, "age": "—", "name": "no runs"}
        row = rows[0]
        ts = _parse_gh_time(row.get("createdAt") or "")
        conc = row.get("conclusion")
        st = row.get("status")
        emoji = "🟢" if conc == "success" else (
            "🔴" if conc in ("failure", "timed_out", "cancelled") else (
                "🟡" if st == "in_progress" else "⚪"
            )
        )
        return {
            "ok": True,
            "emoji": emoji,
            "status": st,
            "conclusion": conc,
            "name": (row.get("displayTitle") or row.get("name") or "?")[:40],
            "branch": row.get("headBranch") or "",
            "age": _ago(ts) if ts else "?",
            "url": row.get("url") or "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:60]}


def _local_signals(key: str, repo: Path) -> str:
    """POPDD / baseline / verify freshness — local deploy truth."""
    bits = []
    # POPDD receipts
    lux = repo / ".lux" / "receipts"
    if lux.is_dir():
        rx = sorted(lux.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if rx:
            bits.append(f"POPDD {_ago(rx[0].stat().st_mtime)}")
    # Hermes reports
    for name in (f"project-next-{key}.md", f"{key}-baseline-", f"signal-m7-"):
        matches = list((_HERMES / "reports").glob(f"{name}*")) if name.endswith("-") else [
            _HERMES / "reports" / name
        ]
        matches = [m for m in matches if m.is_file()]
        if matches:
            newest = max(matches, key=lambda p: p.stat().st_mtime)
            bits.append(f"rpt {_ago(newest.stat().st_mtime)}")
            break
    # Dirty tree
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        dirty = len([l for l in (r.stdout or "").splitlines() if l.strip()])
        bits.append("clean" if dirty == 0 else f"dirty({dirty})")
    except Exception:
        pass
    return " · ".join(bits) if bits else "no local probe"


def _fly_status(app: Optional[str]) -> str:
    if not app:
        return "fly: not wired"
    try:
        r = subprocess.run(
            ["flyctl", "status", "-a", app, "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "")[:50]
            if "not found" in err.lower() or "Error" in err:
                return f"fly:{app} unreachable"
            return f"fly:{app} ?"
        data = json.loads(r.stdout or "{}")
        # shape varies; keep short
        status = data.get("Status") or data.get("status") or "?"
        return f"fly:{app} {status}"
    except FileNotFoundError:
        return "flyctl missing"
    except Exception:
        return f"fly:{app} n/a"


def render_builds() -> Tuple[str, List[ButtonRow]]:
    auth_ok, auth_msg = _gh_auth_ok()
    lines = ["🏗 *Builds & deploys*", ""]
    if not auth_ok:
        lines.append(f"⚠️ GitHub CLI: {auth_msg}")
        lines.append("_Local/deploy probes still below._")
        lines.append("")
    else:
        lines.append("GitHub Actions · local verify · deploy")
        lines.append("")

    fail_url = None
    for key, label, repo, risk, fly_app in _PRODUCTS:
        fence = " 🔐" if risk in ("money", "identity") else ""
        if auth_ok and repo.is_dir():
            run = _gh_latest_run(repo)
            if run.get("ok"):
                emoji = run.get("emoji") or "⚪"
                conc = run.get("conclusion") or run.get("status") or "?"
                lines.append(
                    f"{emoji} *{label}*{fence} · CI `{conc}` · {run.get('age')} ago"
                )
                lines.append(f"   {run.get('name')}")
                if conc in ("failure", "timed_out") and run.get("url"):
                    fail_url = fail_url or run["url"]
            else:
                lines.append(f"⚪ *{label}*{fence} · CI `{run.get('error', '?')[:40]}`")
        else:
            lines.append(f"⚪ *{label}*{fence} · CI `—`")
        local = _local_signals(key if key != "haworks-platform" else "haworks-platform", repo)
        # alias key for reports
        if key == "tie":
            local = _local_signals("tie", repo)
        dep = _fly_status(fly_app)
        lines.append(f"   local: {local}")
        lines.append(f"   deploy: {dep}")
        lines.append("")

    # Estate probe
    try:
        r = subprocess.run(
            ["bash", str(_HERMES / "scripts" / "verify_estate.sh")],
            capture_output=True, text=True, timeout=45,
        )
        verdict = "OPERATIONAL" if r.returncode == 0 else "DEGRADED"
        emoji = "🟢" if r.returncode == 0 else "🔴"
        lines.append(f"{emoji} Estate probe: `{verdict}`")
    except Exception:
        lines.append("⚪ Estate probe: n/a")

    lines.append("")
    if not auth_ok:
        lines.append("→ *Fix: `gh auth login` on this Mac*")
        primary = ("🎛 Mission", "estate:refresh")
    elif fail_url:
        lines.append("→ *Open failing run (link in gh)*")
        primary = ("🔄 Refresh CI", "estate:builds")
    else:
        lines.append("→ *CI clear — refresh anytime*")
        primary = ("🔄 Refresh CI", "estate:builds")

    buttons: List[ButtonRow] = [
        [primary],
        [
            ("🚀 Fleet", "estate:fleet"),
            ("🎛 Mission", "estate:refresh"),
            ("📥 Inbox", "estate:inbox"),
        ],
    ]
    return "\n".join(lines), buttons
