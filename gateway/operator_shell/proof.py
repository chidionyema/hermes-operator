"""Proof loop, idempotent callbacks, and undo audit for operator actions."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _meta_dir() -> Path:
    d = _hermes_home() / "meta" / "operator_shell"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _idem_path() -> Path:
    return _meta_dir() / "idempotency.json"


def _undo_path() -> Path:
    return _meta_dir() / "undo_stack.jsonl"


def _mission_path() -> Path:
    return _meta_dir() / "mission_card.json"


@dataclass
class Proof:
    """Outcome receipt for an operator action."""

    action: str
    status: str  # done | failed | noop | pending_confirm
    summary: str
    request_id: str
    cost_usd: Optional[float] = None
    evidence: List[str] = field(default_factory=list)
    undoable: bool = False
    undo_token: Optional[str] = None
    ts: float = field(default_factory=time.time)

    def render(self) -> str:
        lines = [f"✅ *{self.status.upper()}* — {self.summary}" if self.status == "done"
                 else f"⚠️ *{self.status.upper()}* — {self.summary}"]
        if self.cost_usd is not None:
            lines.append(f"· ${self.cost_usd:.4f}")
        for ev in self.evidence[:4]:
            lines.append(f"· {ev}")
        lines.append(f"· `rid:{self.request_id[:8]}`")
        if self.undoable and self.undo_token:
            lines.append(f"· undo: `/undo {self.undo_token[:8]}`")
        return "\n".join(lines)


def new_request_id() -> str:
    return uuid.uuid4().hex


def check_idempotent(request_id: str, ttl_s: float = 120.0) -> Optional[Dict[str, Any]]:
    """Return prior result if this request_id was already handled within ttl."""
    if not request_id:
        return None
    path = _idem_path()
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
    except Exception:
        data = {}
    entry = data.get(request_id)
    if not entry:
        return None
    if time.time() - float(entry.get("ts", 0)) > ttl_s:
        return None
    return entry.get("result")


def store_idempotent(request_id: str, result: Dict[str, Any]) -> None:
    if not request_id:
        return
    path = _idem_path()
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
    except Exception:
        data = {}
    # prune old
    now = time.time()
    data = {k: v for k, v in data.items() if now - float(v.get("ts", 0)) < 600}
    data[request_id] = {"ts": now, "result": result}
    path.write_text(json.dumps(data))


def push_undo(action: str, reverse: Dict[str, Any], summary: str) -> str:
    """Append undo record; return undo token."""
    token = uuid.uuid4().hex[:12]
    rec = {
        "token": token,
        "ts": time.time(),
        "action": action,
        "reverse": reverse,
        "summary": summary,
    }
    with open(_undo_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    # also mirror into coordinator events if available
    try:
        import importlib.util
        import sys

        scripts = str(_hermes_home() / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import coordinator as C  # type: ignore

        conn = C.connect()
        try:
            C.add_event(conn, "operator", "undoable", f"{action}: {summary}", json.dumps(rec))
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("undo audit to coordinator skipped: %s", exc)
    return token


def pop_undo(token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return and consume the matching (or latest) undo record."""
    path = _undo_path()
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    if not records:
        return None
    chosen = None
    if token:
        for r in reversed(records):
            if str(r.get("token", "")).startswith(token):
                chosen = r
                break
    else:
        chosen = records[-1]
    if not chosen:
        return None
    # rewrite without chosen
    keep = [r for r in records if r.get("token") != chosen.get("token")]
    path.write_text("".join(json.dumps(r) + "\n" for r in keep[-50:]), encoding="utf-8")
    return chosen


def load_mission_card() -> Dict[str, Any]:
    path = _mission_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_mission_card(chat_id: str, message_id: str, thread_id: Optional[str] = None) -> None:
    path = _mission_path()
    path.write_text(
        json.dumps(
            {
                "chat_id": str(chat_id),
                "message_id": str(message_id),
                "thread_id": str(thread_id) if thread_id else None,
                "updated_at": time.time(),
            }
        )
    )


def proof_to_dict(p: Proof) -> Dict[str, Any]:
    return asdict(p)
