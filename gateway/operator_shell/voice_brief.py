"""Voice / short text → 5-line executive brief + buttons (not a novel)."""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

_BRIEF_TRIGGERS = re.compile(
    r"^\s*(brief|status|what'?s\s+going\s+on|whats\s+going\s+on|"
    r"how'?s\s+it\s+going|how\s+are\s+things|executive\s+brief|summary|"
    r"morning|update\s+me|how\s+are\s+we|catch\s+me\s+up|fill\s+me\s+in|"
    r"sitrep|rundown)\b",
    re.I,
)


def wants_executive_brief(text: str, *, from_voice: bool = False) -> bool:
    if not text or not text.strip():
        return False
    if from_voice and len(text.strip()) < 200:
        # Voice notes default to brief unless clearly a long tasking
        if re.search(r"\b(implement|refactor|write|code|debug|fix)\b", text, re.I):
            return False
        return True
    return bool(_BRIEF_TRIGGERS.search(text.strip()))


def render_executive_brief() -> Tuple[str, List[ButtonRow]]:
    from gateway.operator_shell.mission import render_mission_card
    from gateway.operator_shell.inbox import render_inbox
    from gateway.operator_shell.budget import check_budget

    mission, paused, buttons = render_mission_card()
    inbox_text, _ = render_inbox()
    ok, bmsg, metrics = check_budget()
    lines = [
        "🎙 *Executive brief*",
        "",
        mission.split("\n")[0],  # verdict line
    ]
    for line in mission.splitlines():
        if line.startswith("💰") or line.startswith("🧱") or line.startswith("🧠"):
            lines.append(line)
    ask_line = "—"
    if "need you" in inbox_text.lower() or "APPROVE" in inbox_text:
        for line in inbox_text.splitlines():
            if "APPROVE" in line or "BLOCKED" in line:
                ask_line = line.strip()
                break
    lines.append(f"📥 {ask_line}")
    if not ok:
        lines.append(f"🛑 {bmsg}")
    elif metrics:
        lines.append(
            f"⛽ {metrics.get('tasks', '?')} tasks · ${metrics.get('cost_usd', 0):.2f}"
        )
    # Brief buttons: keep mission row0 + RSI shortcut
    brief_buttons: List[ButtonRow] = [
        buttons[0] if buttons else [("🎛 Mission", "estate:refresh")],
        [
            ("🧠 RSI", "estate:rsi"),
            ("📥 Inbox", "estate:inbox"),
            ("🎛 Mission", "estate:refresh"),
        ],
    ]
    return "\n".join(lines), brief_buttons
