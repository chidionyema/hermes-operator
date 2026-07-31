"""CEO natural language → structured estate actions (no freeform guessing).

Keep patterns SHORT and anchored. Long tasking ("rewrite prospector…") must
return None so Otto inject / agent can run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class NaturalOp:
    action: str  # estate action (or action:arg)
    args: str = ""
    proof_label: str = ""


# Order matters: first match wins. Prefer specific (approve id) before broad (status).
_PATTERNS = [
    # Spend / estate power
    (re.compile(
        r"^\s*(pause\s+(all\s+)?spend|pause\s+estate|freeze\s+spend|"
        r"pause\s+everything|stop\s+spending)\s*$", re.I),
     "pause", "", "Pause spend"),
    (re.compile(
        r"^\s*(resume\s+(spend|estate)?|unfreeze|unpause|go\s+live|"
        r"resume\s+everything)\s*$", re.I),
     "resume", "", "Resume spend"),
    # Mission / status / sitrep
    (re.compile(
        r"^\s*(what'?s\s+on\s+fire|on\s+fire|status|mission|cockpit|panel|"
        r"health|are\s+we\s+(ok|good|clear)|all\s+good|everything\s+ok|"
        r"how'?s\s+the\s+(estate|ship|system))\s*\??\s*$", re.I),
     "refresh", "", "Mission card"),
    (re.compile(
        r"^\s*(brief|briefing|sitrep|sit[- ]?rep|rundown|catch\s+me\s+up|"
        r"fill\s+me\s+in|update\s+me|what'?s\s+going\s+on|whats\s+going\s+on|"
        r"how'?s\s+it\s+going|how\s+are\s+we|how\s+are\s+things|"
        r"executive\s+brief|summary)\s*\??\s*$", re.I),
     "brief", "", "Executive brief"),
    # Inbox / decisions
    (re.compile(
        r"^\s*(inbox|decisions?|approvals?|what\s+needs\s+me|"
        r"needs\s+(my|your)\s+(call|approval)|waiting\s+on\s+me|"
        r"what'?s\s+blocked|whats\s+blocked)\s*\??\s*$", re.I),
     "inbox", "", "Inbox"),
    # Approve short id
    (re.compile(r"^\s*approve\s+`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "approve", "{g1}", "Approve"),
    # Fleet / missions
    (re.compile(
        r"^\s*(fleet|projects?|portfolio)\s*\??\s*$", re.I),
     "fleet", "", "Fleet"),
    (re.compile(
        r"^\s*(builds?|ci|cicd|ci\/?cd|deploys?|ship\s+status|"
        r"github\s+actions?|deploy\s+status)\s*\??\s*$", re.I),
     "builds", "", "Builds"),
    (re.compile(
        r"^\s*(missions?|mission\s+board|autopilot)\s*\??\s*$", re.I),
     "missions", "", "Missions"),
    # RSI / learning
    (re.compile(
        r"^\s*(rsi|learning|self[-\s]?improv\w*|are\s+you\s+learning|"
        r"are\s+you\s+improving|how\s+are\s+you\s+improving|"
        r"self[-\s]?improv\w*\s+status|rsi\s+status)\s*\??\s*$", re.I),
     "rsi", "", "RSI"),
    (re.compile(
        r"^\s*(arm\s+(self[-\s]?improv\w*|learning|rsi)|"
        r"enable\s+(self[-\s]?improv\w*|learning|rsi))\s*$", re.I),
     "arm_learning", "", "Arm learning"),
    (re.compile(
        r"^\s*(disarm\s+(self[-\s]?improv\w*|learning|rsi)|"
        r"disable\s+(self[-\s]?improv\w*|learning|rsi))\s*$", re.I),
     "disarm_learning", "", "Disarm learning"),
    # Estate / Prospector daemons (before generic "run prospector")
    (re.compile(
        r"^\s*(daemons?|services?|launchctl|estate\s+daemons?)\s*\??\s*$", re.I),
     "daemons", "", "Daemons"),
    (re.compile(
        r"^\s*(prospector\s+daemons?|prospect\s+daemons?|"
        r"prospector\s+(status|health)|daemon\s+status\s+prospector|"
        r"how's\s+prospector(\s+daemon)?|how\s+is\s+prospector(\s+daemon)?)\s*\??\s*$",
        re.I),
     "prospector_daemon", "", "Prospector daemons"),
    (re.compile(
        r"^\s*restart\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_restart", "scheduler", "Restart Prospector scheduler"),
    (re.compile(
        r"^\s*start\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_start", "scheduler", "Start Prospector scheduler"),
    (re.compile(
        r"^\s*stop\s+prospector(\s+daemon|\s+scheduler|\s+sched)?\s*$", re.I),
     "pd_stop", "scheduler", "Stop Prospector scheduler"),
    (re.compile(
        r"^\s*(run|fire|kick)\s+prospector\s+watchdog\s*(now)?\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*restart\s+prospector\s+watchdog\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*start\s+prospector\s+watchdog\s*$", re.I),
     "pd_run_now", "watchdog", "Run Prospector watchdog now"),
    (re.compile(
        r"^\s*(stop|unload)\s+prospector\s+watchdog\s*$", re.I),
     "pd_stop", "watchdog", "Unload Prospector watchdog"),
    (re.compile(
        r"^\s*prospector\s+(logs?|log\s*tail|errors?)\s*$", re.I),
     "pd_logs", "scheduler", "Prospector logs"),
    (re.compile(
        r"^\s*prospector\s+(params?|settings?|knobs|interval|flags)\s*\??\s*$", re.I),
     "pd_params", "", "Prospector params"),
    (re.compile(
        r"^\s*prospector\s+(cron|schedule|outcomes?|ticks?)\s*\??\s*$", re.I),
     "pd_cron", "", "Prospector cron"),
    (re.compile(
        r"^\s*pause\s+prospector(\s+gen(eration)?)?\s*$", re.I),
     "pd_pause", "", "Pause Prospector gen"),
    (re.compile(
        r"^\s*(unpause|resume)\s+prospector(\s+gen(eration)?)?\s*$", re.I),
     "pd_unpause", "", "Resume Prospector gen"),
    (re.compile(
        r"^\s*set\s+prospector\s+(interval|concurrency|batch_size|daily_cap)\s+(\d+)\s*$",
        re.I),
     "pd_set", "{g1}:{g2}", "Set Prospector param"),
    # Ops
    (re.compile(r"^\s*(stop\s+(the\s+)?agent|kill\s+(the\s+)?run|halt)\s*$", re.I),
     "stop_agent", "", "Stop agent"),
    (re.compile(r"^\s*run\s+prospector(?:\s+(\d+))?\s*$", re.I),
     "run_prospector", "{g1}", "Run prospector"),
    (re.compile(r"^\s*(undo|rollback)\s*$", re.I), "undo", "", "Undo"),
    (re.compile(
        r"^\s*(budget|spend\s+today|burn|fuel)\s*\??\s*$", re.I),
     "system_fuel", "", "Fuel"),
    # Coding run status / cancel (short pulls only — long tasking goes to code_remote)
    (re.compile(
        r"^\s*(?:task|run|job|how'?s\s+(?:that\s+)?task)\s+`?([0-9a-fA-F]{4,12})`?\s*\??\s*$",
        re.I),
     "task", "{g1}", "Task status"),
    (re.compile(
        r"^\s*(?:status\s+of\s+(?:task\s+)?|how'?s\s+)\s*`?([0-9a-fA-F]{4,12})`?\s*\??\s*$",
        re.I),
     "task", "{g1}", "Task status"),
    (re.compile(
        r"^\s*cancel\s+(?:task\s+)?`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "cancel", "{g1}", "Cancel task"),
    (re.compile(
        r"^\s*pause\s+(?:task\s+)?`?([0-9a-fA-F]{4,12})`?\s*$", re.I),
     "pause_task", "{g1}", "Pause task"),
]


def match_natural_op(text: str) -> Optional[NaturalOp]:
    """Return a structured op if text is a short CEO command; else None."""
    if not text or len(text) > 140:
        return None
    raw = text.strip()
    # Never intercept slash commands or Otto task injections
    if raw.startswith("/"):
        return None
    if re.match(r"^\s*otto[,:]?\s+\S", raw, re.I):
        # Allow "Otto status" / "Otto, status" as CEO pulls (strip address)
        stripped = re.sub(r"^\s*otto[,:]?\s+", "", raw, flags=re.I).strip()
        # If remaining looks like a task (long / verb-heavy), don't intercept
        if len(stripped.split()) > 8:
            return None
        raw = stripped
    for pat, action, args_tmpl, label in _PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        args = args_tmpl
        if m.lastindex:
            for i in range(1, m.lastindex + 1):
                args = args.replace(f"{{g{i}}}", m.group(i) or "")
        return NaturalOp(action=action, args=args, proof_label=label)
    return None
