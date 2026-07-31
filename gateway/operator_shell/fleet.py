"""Project fleet tiles — prospector / signal / TIE / haworks."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_KEYS = (
    ("prospector", "Prospector"),
    ("signalengine", "Signal"),
    ("tie", "TIE"),
    ("haworks-platform", "Haworks"),
)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _load_projects() -> List[Dict[str, Any]]:
    path = _hermes_home() / "projects.json"
    try:
        data = json.loads(path.read_text())
        return list(data.get("projects") or [])
    except Exception:
        return []


def _repo_health() -> Dict[str, Dict[str, Any]]:
    path = _hermes_home() / "logs" / "health" / "repo-health.jsonl"
    out: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-80:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            name = str(row.get("repo") or row.get("name") or "").lower()
            if name:
                out[name] = row
    except Exception:
        pass
    return out


def _git_short(repo: Path) -> str:
    if not repo.is_dir():
        return "missing"
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dirty = len([l for l in (r.stdout or "").splitlines() if l.strip()])
        return "clean" if dirty == 0 else f"dirty({dirty})"
    except Exception:
        return "unverified"


def _status_report(key: str) -> str:
    path = _hermes_home() / "reports" / f"project-status-{key}.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.strip().startswith("-") or "blocker" in line.lower():
                return line.strip()[:60]
        return text.strip().splitlines()[0][:60] if text.strip() else ""
    except Exception:
        return ""


def _inflight(key: str) -> int:
    try:
        from gateway.operator_shell.estate import _load_coordinator

        C = _load_coordinator()
        conn = C.connect()
        try:
            if hasattr(C, "project_task_inflight"):
                return int(C.project_task_inflight(conn, key) or 0)
            rows = C.backlog_view(conn)
            return sum(1 for r in rows if key in str(r.get("title", "")).lower())
        finally:
            conn.close()
    except Exception:
        return 0


def render_fleet() -> Tuple[str, List[ButtonRow]]:
    projects = {p.get("key"): p for p in _load_projects()}
    health = _repo_health()
    lines = ["🚀 *Fleet*", ""]
    for key, label in _KEYS:
        p = projects.get(key) or {}
        repo = Path(str(p.get("repo") or "").replace("~", str(Path.home()))).expanduser()
        git = _git_short(repo) if repo.parts else "n/a"
        h = health.get(key) or health.get(repo.name.lower() if repo.parts else "") or {}
        state = h.get("state") or git
        inflight = _inflight(key)
        blocker = _status_report(key) or ("—" if state in ("clean", "pass") else str(state))
        emoji = "🟢" if state in ("clean", "pass", "ok") and inflight == 0 else (
            "🟡" if inflight else "🔴" if "dirty" in str(state) or state == "fail" else "⚪"
        )
        lines.append(f"{emoji} *{label}* · {state} · inflight {inflight}")
        lines.append(f"   next/blocker: {blocker[:55]}")
        lines.append("")

    buttons: List[ButtonRow] = [
        [
            ("🏗 Builds", "estate:builds"),
            ("⚙️ Prospect daemons", "estate:prospector_daemon"),
        ],
        [
            ("⚡️ Run Prospector", "estate:run_prospector"),
            ("⚙️ Estate daemons", "estate:daemons"),
        ],
        [
            ("📥 Inbox", "estate:inbox"),
            ("🎛 Mission", "estate:refresh"),
        ],
    ]
    # Prefixed glance for Prospector daemon health
    try:
        from gateway.operator_shell.prospector_daemon import glance_line

        glance = glance_line()
        if glance:
            lines.insert(1, glance)
            lines.insert(2, "")
    except Exception:
        pass
    return "\n".join(lines).rstrip(), buttons
