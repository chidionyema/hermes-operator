"""Claude Code remote — Telegram assign + monitor durable coding runs.

Uses the estate's existing coordinator executor (claude -p → agy → route fallback).
Phone UX: start via `Otto code` / `cc`, live progress edits, `task <id>` card.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_FENCE_RE = re.compile(
    r"\b(signalengine|signal engine|tie\b|introduction exchange|stripe|payout|"
    r"settlement|kyc|identity|money|billing|live order)\b",
    re.I,
)

_CODE_PREFIXES = re.compile(
    r"^\s*(?:otto[,:]?\s+)?(?:code|cc|claude\s*code|coding)\b[:\s,-]*",
    re.I,
)


def is_code_command(text: str) -> Optional[str]:
    """If text is a coding assign command, return the task body; else None."""
    raw = (text or "").strip()
    if not raw:
        return None
    # cc <task> or Otto code <task> or Otto, code: <task>
    m = re.match(
        r"^\s*(?:otto[,:]?\s+)?(?:code|cc|claude\s*code)\b[:\s,-]*(.+)$",
        raw,
        re.I | re.DOTALL,
    )
    if not m:
        return None
    body = (m.group(1) or "").strip()
    return body if len(body) >= 3 else None


def is_task_query(text: str) -> Optional[str]:
    """Return short task id if text asks about a coding/run task."""
    raw = (text or "").strip()
    m = re.match(
        r"^\s*(?:otto[,:]?\s+)?(?:task|run|job|how'?s\s+(?:that\s+)?task)\b"
        r"\s*`?([0-9a-fA-F]{4,12})`?\s*\??\s*$",
        raw,
        re.I,
    )
    if m:
        return m.group(1)
    m2 = re.match(
        r"^\s*(?:how'?s\s+(?:that|the|my)\s+(?:task|run|job)|"
        r"status\s+of\s+(?:task\s+)?`?([0-9a-fA-F]{4,12})`?)\s*\??\s*$",
        raw,
        re.I,
    )
    if m2 and m2.lastindex:
        return m2.group(1)
    return None


def _coord():
    from gateway.operator_shell.estate import _load_coordinator

    return _load_coordinator()


def quota_honesty() -> Tuple[bool, str]:
    """Return (can_run_tools, message). Immediate honesty before fake progress."""
    C = _coord()
    claude_ok = True
    agy_ok = True
    try:
        claude_ok = C._circuit_breaker_status("claude")
        agy_ok = C._circuit_breaker_status("agy")
    except Exception as exc:
        # A failed probe is not a healthy breaker. Say so rather than
        # reporting "ready" off a default we never confirmed.
        logger.warning("circuit-breaker probe failed: %s", exc)
        return True, f"⚠️ Breaker state unknown (probe failed: {type(exc).__name__}) — proceeding"
    if claude_ok:
        return True, "Claude Code CLI ready"
    if agy_ok:
        return True, "Claude quota/CB open — will use agy / DeepSeek-Minimax fallback"
    return (
        False,
        "⛔ Claude + agy circuit-breakers OPEN (quota/rate-limit). "
        "Run will queue and use chat fallback when possible — no fake tool progress.",
    )


def detect_fence(body: str) -> Optional[str]:
    if _FENCE_RE.search(body or ""):
        # Prefer identity for TIE, money otherwise
        if re.search(r"\b(tie|introduction exchange|kyc|identity)\b", body or "", re.I):
            return "identity"
        return "money"
    return None


def _norm_body(body: str) -> str:
    return re.sub(r"\s+", " ", (body or "").strip().lower())[:200]


def find_inflight_code_runs(conn, C, limit: int = 5):
    """Active coding runs (for panel CTA + dedupe)."""
    rows = conn.execute(
        "SELECT * FROM tasks WHERE source='code:telegram' "
        "AND status IN ('open','diagnosed','executing','verifying','awaiting_approval') "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(rows)


def start_code_run(body: str, created_by: str = "telegram") -> Tuple[str, str, List[ButtonRow]]:
    """Create durable coding task (idempotent within 10m for same body)."""
    C = _coord()
    conn = C.connect()
    try:
        body = (body or "").strip()
        norm = _norm_body(body)
        # Idempotent: same text within 10 minutes → resume existing run
        cutoff = time.time() - 600
        for row in find_inflight_code_runs(conn, C, limit=20):
            existing_body = (row["body"] or "")
            # body stored with header; match TASK section or title
            if norm and (norm in _norm_body(existing_body) or norm in _norm_body(row["title"] or "")):
                if (row["created_at"] or 0) >= cutoff or row["status"] in (
                    "executing", "diagnosed", "verifying", "awaiting_approval"
                ):
                    tid = row["id"]
                    text, buttons = render_task_card(tid[:8])
                    ack = (
                        f"↻ *Resumed existing run* `{tid[:8]}` "
                        f"(same ask — no duplicate)\n\n" + text
                    )
                    return ack, tid, buttons

        can_run, qmsg = quota_honesty()
        fence = detect_fence(body)
        title = f"💻 CODE: {body[:100]}"
        tid = C.open_task(
            conn,
            title=title,
            body=(
                "CODING RUN (Claude Code remote via claude -p / agy).\n"
                "Do real file edits + tests. Cite evidence. No fabrication.\n\n"
                f"TASK:\n{body}"
            ),
            kind="injected",
            source="code:telegram",
            created_by=created_by,
        )
        if fence:
            C._set(conn, tid, status="awaiting_approval", risk_class=fence)
            C.add_event(conn, tid, "fence", f"{fence} — founder APPROVE before mutate")
            ack = (
                f"⏸️ *Fenced ({fence})* — not started\n"
                f"· task `{tid[:8]}`\n"
                f"· {body[:80]}\n"
                f"· Tap ✅ APPROVE to run\n"
                f"· `{qmsg}`"
            )
            buttons = [
                [
                    (f"✅ APPROVE {tid[:8]}", f"estate:approve:{tid[:8]}"),
                    (f"👁 Status", f"estate:task:{tid[:8]}"),
                ],
                [("📥 Inbox", "estate:inbox"), ("🎛 Mission", "estate:refresh")],
            ]
            C.progress_notify(conn, C.get_task(conn, tid), ack)
            return ack, tid, buttons

        phase = "queued" if can_run else "queued (quota — fallback path)"
        card = format_progress_card(
            tid, title, phase=phase, detail=qmsg, status="open",
            blocker="" if can_run else "Waiting on Claude/agy CB; will use fallback",
        )
        C.progress_notify(conn, C.get_task(conn, tid), card)
        ack = (
            f"💻 *Coding run* `{tid[:8]}`\n"
            f"· {body[:90]}\n"
            f"· {qmsg}\n"
            f"· Live progress above · buttons to cancel/steer"
        )
        buttons = [
            [
                (f"👁 {tid[:8]}", f"estate:task:{tid[:8]}"),
                (f"🛑 Cancel", f"estate:cancel:{tid[:8]}"),
            ],
            [("⏸ Pause", f"estate:pause_task:{tid[:8]}"), ("🎛 Mission", "estate:refresh")],
        ]
        return ack, tid, buttons
    finally:
        conn.close()


def format_progress_card(
    tid: str,
    title: str,
    *,
    phase: str,
    detail: str = "",
    status: str = "",
    files: str = "",
    blocker: str = "",
) -> str:
    lines = [
        f"💻 *Run* `{tid[:8]}` · `{status or phase}`",
        f"{(title or '')[:70]}",
        f"Phase: *{phase}*",
    ]
    if detail:
        lines.append(detail[:140])
    if files:
        lines.append(f"Files: `{files[:80]}`")
    if blocker:
        lines.append(f"🧱 {blocker[:100]}")
    lines.append(f"_`task {tid[:8]}` · cancel / steer_")
    return "\n".join(lines)


def render_task_card(ref: str) -> Tuple[str, List[ButtonRow]]:
    C = _coord()
    conn = C.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE id LIKE ? ORDER BY created_at DESC LIMIT 1",
            (f"{ref}%",),
        ).fetchall()
        if not rows:
            return (
                f"No task matching `{ref}`",
                [[("🎛 Mission", "estate:refresh"), ("📥 Inbox", "estate:inbox")]],
            )
        t = rows[0]
        tid = t["id"]
        st = t["status"]
        title = t["title"] or ""
        result = (t["result"] or "")[:200]
        err = (t["last_failure_error"] or "")[:120]
        # Infer phase
        phase = {
            "open": "diagnosing",
            "diagnosed": "ready",
            "executing": "claude/agy working",
            "verifying": "verifying",
            "awaiting_approval": "fenced — need APPROVE",
            "escalated": "blocked",
            "done": "done",
        }.get(st, st)
        files = ""
        if result:
            # crude file path scrape
            paths = re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|go|rs|md|yml|yaml)", result)
            if paths:
                # dict.fromkeys dedupes and preserves order, but a dict is not
                # sliceable — materialise it before taking the first few.
                files = ", ".join(list(dict.fromkeys(paths))[:4])
        blocker = err or (
            "money/identity fence" if st == "awaiting_approval" else ""
        )
        can_run, qmsg = quota_honesty()
        if st == "executing" and not can_run:
            blocker = blocker or qmsg
        text = format_progress_card(
            tid,
            title,
            phase=phase,
            detail=qmsg if st in ("open", "diagnosed", "executing") else result[:100],
            status=st,
            files=files,
            blocker=blocker,
        )
        buttons: List[ButtonRow] = []
        if st == "awaiting_approval":
            buttons.append(
                [
                    (f"✅ APPROVE {tid[:8]}", f"estate:approve:{tid[:8]}"),
                    (f"🛑 Cancel", f"estate:cancel:{tid[:8]}"),
                ]
            )
        elif st in ("open", "diagnosed", "executing", "verifying"):
            buttons.append(
                [
                    (f"🛑 Cancel {tid[:8]}", f"estate:cancel:{tid[:8]}"),
                    (f"⏸ Pause", f"estate:pause_task:{tid[:8]}"),
                ]
            )
            buttons.append(
                [(f"➕ Steer", f"estate:steer_prompt:{tid[:8]}")]
            )
        elif st == "escalated":
            buttons.append(
                [
                    (f"✅ Retry {tid[:8]}", f"estate:approve:{tid[:8]}"),
                    (f"🛑 Cancel", f"estate:cancel:{tid[:8]}"),
                ]
            )
        buttons.append(
            [("🔄 Refresh", f"estate:task:{tid[:8]}"), ("🎛 Mission", "estate:refresh")]
        )
        return text, buttons
    finally:
        conn.close()


def cancel_task(ref: str) -> Tuple[str, List[ButtonRow]]:
    C = _coord()
    conn = C.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE id LIKE ? LIMIT 1", (f"{ref}%",)
        ).fetchall()
        if not rows:
            return f"No task `{ref}`", [[("🎛 Mission", "estate:refresh")]]
        t = rows[0]
        tid = t["id"]
        if t["status"] == "done":
            return f"`{tid[:8]}` already done", [[("🎛 Mission", "estate:refresh")]]
        C.add_event(conn, tid, "cancelled", "founder cancel via Telegram")
        C._set(conn, tid, status="done", completed_at=time.time(),
               result=(t["result"] or "") + "\n[cancelled by founder]")
        # Drop from executor pool if present
        try:
            C._EXECUTORS.pop(tid, None)
        except Exception:
            pass
        msg = f"🛑 Cancelled `{tid[:8]}` — {t['title'][:50]}"
        C.progress_notify(conn, C.get_task(conn, tid) or t, msg)
        return msg, [[("🎛 Mission", "estate:refresh"), ("📥 Inbox", "estate:inbox")]]
    finally:
        conn.close()


def pause_task(ref: str) -> Tuple[str, List[ButtonRow]]:
    """Park an active coding task at awaiting_approval (founder resume via approve)."""
    C = _coord()
    conn = C.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE id LIKE ? LIMIT 1", (f"{ref}%",)
        ).fetchall()
        if not rows:
            return f"No task `{ref}`", [[("🎛 Mission", "estate:refresh")]]
        t = rows[0]
        tid = t["id"]
        if t["status"] not in ("open", "diagnosed", "executing", "verifying"):
            return f"`{tid[:8]}` not pausable ({t['status']})", [[("👁 Status", f"estate:task:{tid[:8]}")]]
        try:
            C._EXECUTORS.pop(tid, None)
        except Exception:
            pass
        C.add_event(conn, tid, "paused", "founder pause")
        C._set(conn, tid, status="awaiting_approval", risk_class=t["risk_class"] or "low")
        msg = f"⏸ Paused `{tid[:8]}` — approve to resume"
        C.progress_notify(conn, C.get_task(conn, tid) or t, msg)
        return msg, [
            [
                (f"✅ Resume {tid[:8]}", f"estate:approve:{tid[:8]}"),
                (f"🛑 Cancel", f"estate:cancel:{tid[:8]}"),
            ]
        ]
    finally:
        conn.close()


def steer_prompt_card(ref: str) -> Tuple[str, List[ButtonRow]]:
    """Tell founder how to steer (append instruction via Otto code steer)."""
    return (
        f"➕ *Steer* `{ref}`\n\n"
        f"Reply: `Otto steer {ref} <instruction>`\n"
        f"Example: `Otto steer {ref} only touch tests, no prod code`",
        [
            [(f"👁 Status", f"estate:task:{ref}")],
            [("🎛 Mission", "estate:refresh")],
        ],
    )


def steer_task(ref: str, instruction: str) -> Tuple[str, List[ButtonRow]]:
    C = _coord()
    conn = C.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE id LIKE ? LIMIT 1", (f"{ref}%",)
        ).fetchall()
        if not rows:
            return f"No task `{ref}`", [[("🎛 Mission", "estate:refresh")]]
        t = rows[0]
        tid = t["id"]
        body = (t["body"] or "") + f"\n\nSTEER ({time.strftime('%H:%M')}): {instruction.strip()}"
        C._set(conn, tid, body=body)
        # Re-queue if done/escalated
        if t["status"] in ("done", "escalated", "awaiting_approval"):
            C._set(conn, tid, status="diagnosed", consecutive_failures=0)
        elif t["status"] == "executing":
            # Let current finish; next diagnose will see steer — force re-diagnose
            try:
                C._EXECUTORS.pop(tid, None)
            except Exception:
                pass
            C._set(conn, tid, status="diagnosed")
        C.add_event(conn, tid, "steer", instruction[:300])
        msg = f"➕ Steered `{tid[:8]}` — {instruction[:80]}"
        C.progress_notify(conn, C.get_task(conn, tid) or t, msg)
        text, buttons = render_task_card(tid[:8])
        return msg + "\n\n" + text, buttons
    finally:
        conn.close()


def parse_steer(text: str) -> Optional[Tuple[str, str]]:
    m = re.match(
        r"^\s*(?:otto[,:]?\s+)?steer\s+`?([0-9a-fA-F]{4,12})`?\s+(.+)$",
        (text or "").strip(),
        re.I | re.DOTALL,
    )
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def is_natural_code_assign(text: str) -> Optional[str]:
    """Plain-English coding assign without `code`/`cc` prefix.

    Examples:
      fix the login bug in prospector
      Otto, implement dark mode on POPDD
      refactor the funnel in prospector and add tests
    """
    raw = (text or "").strip()
    if not raw or len(raw) < 12 or len(raw) > 500:
        return None
    # Strip Otto address but keep the rest as the body
    body = re.sub(r"^\s*otto[,:]?\s+", "", raw, flags=re.I).strip()
    # Must look like a coding verb + target product/path
    if not re.match(
        r"^(?:fix|implement|refactor|patch|debug|wire|ship|add|update|rewrite|"
        r"migrate|harden|unblock|port|test)\b",
        body,
        re.I,
    ):
        return None
    # Need a repo/product anchor (in/on/for <name>) OR explicit file-ish token
    if not (
        re.search(
            r"\b(?:in|on|for)\s+(?:prospector|popdd|signal|tie|hermes|"
            r"[\w.-]+\.(?:py|ts|tsx|js|go|rs|md))\b",
            body,
            re.I,
        )
        or re.search(r"\b(?:prospector|popdd)\b", body, re.I)
    ):
        return None
    # Don't steal CEO short pulls
    if len(body.split()) < 4:
        return None
    return body
