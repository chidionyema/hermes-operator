"""Prospector daemon phone control — com.prospector.* LaunchAgents.

THE durable daemon is `com.prospector.scheduler` (KeepAlive, --daemon --interval 7200).
`com.prospector.watchdog` is a StartInterval=900 *oneshot* — it runs, checks, exits.
Showing it as 🔴 "not running" after start was the UX lie that made controls look broken.

Honest: plist missing → not installed. Interval jobs → armed / last-exit, not KeepAlive.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]

REPO = Path.home() / "Documents" / "code" / "prospector"
PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
STORE = REPO / "store" / "scheduler"

# kind: keepalive | interval | ui
_UNITS: Tuple[Tuple[str, str, str, str, Tuple[Path, ...]], ...] = (
    (
        "com.prospector.scheduler",
        "scheduler",
        "keepalive",
        "generation daemon — real ticks (KeepAlive)",
        (STORE / "launchd.err.log", STORE / "launchd.out.log", Path("/tmp/prospector_gen.log")),
    ),
    (
        "com.prospector.watchdog",
        "watchdog",
        "interval",
        "15-min oneshot check (not a long-running process)",
        (STORE / "watchdog.err.log", STORE / "watchdog.out.log"),
    ),
    (
        "com.prospector.control-center",
        "control-center",
        "ui",
        "local Streamlit UI :8601 (KeepAlive)",
        (Path("/tmp/prospector_control_center.log"),),
    ),
)

_KIND = {u[0]: u[2] for u in _UNITS}
_SHORT_TO_LABEL = {u[1]: u[0] for u in _UNITS}
_SHORT_TO_LABEL.update(
    {
        "daemon": "com.prospector.scheduler",
        "gen": "com.prospector.scheduler",
        "prospect": "com.prospector.scheduler",
        "prospector": "com.prospector.scheduler",
        "sched": "com.prospector.scheduler",
        "cc": "com.prospector.control-center",
        "ui": "com.prospector.control-center",
        "watch": "com.prospector.watchdog",
    }
)


def _uid() -> int:
    return os.getuid()  # windows-footgun: ok — POSIX launchd (macOS) helper, never invoked on Windows


def installed(label: str) -> bool:
    return (PLIST_DIR / f"{label}.plist").is_file()


def launchctl_state(label: str) -> Dict[str, object]:
    plist = PLIST_DIR / f"{label}.plist"
    kind = _KIND.get(label, "keepalive")
    if not plist.is_file():
        return {
            "running": False,
            "pid": None,
            "state": "not_installed",
            "detail": "plist missing",
            "installed": False,
            "kind": kind,
            "armed": False,
        }
    target = f"gui/{_uid()}/{label}"
    try:
        r = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and (
            "Could not find service" in out or "not be found" in out.lower()
        ):
            return {
                "running": False,
                "pid": None,
                "state": "unloaded",
                "detail": "plist on disk, not loaded",
                "installed": True,
                "kind": kind,
                "armed": False,
                "last_exit": None,
                "runs": None,
            }
        state = "unknown"
        pid: Optional[int] = None
        last_exit: Optional[str] = None
        runs: Optional[int] = None
        for ln in out.splitlines():
            s = ln.strip()
            if s.startswith("state ="):
                state = s.split("=", 1)[1].strip()
            if s.startswith("pid ="):
                try:
                    pid = int(s.split("=", 1)[1].strip())
                except Exception:
                    pid = None
            if s.startswith("last exit code ="):
                last_exit = s.split("=", 1)[1].strip()
            if s.startswith("runs ="):
                try:
                    runs = int(s.split("=", 1)[1].strip())
                except Exception:
                    runs = None
        running = state == "running" or (pid is not None and pid > 0 and state != "not running")
        armed = True  # in launchd domain
        # Interval oneshots are healthy when armed even if not currently running
        if kind == "interval":
            detail = f"armed · last exit {last_exit or '?'} · runs {runs or 0}"
            if running:
                detail = f"running now pid {pid} · " + detail
        else:
            detail = f"pid {pid}" if (running and pid) else state
        return {
            "running": running,
            "pid": pid,
            "state": state,
            "detail": detail,
            "installed": True,
            "kind": kind,
            "armed": armed,
            "last_exit": last_exit,
            "runs": runs,
        }
    except Exception as exc:
        return {
            "running": False,
            "pid": None,
            "state": "error",
            "detail": str(exc)[:50],
            "installed": True,
            "kind": kind,
            "armed": False,
        }


def _ago(ts: float) -> str:
    age = max(0, int(time.time() - ts))
    if age < 90:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86400:
        return f"{age // 3600}h"
    return f"{age // 86400}d"


def _heartbeat() -> Dict[str, object]:
    path = STORE / "heartbeat.json"
    if not path.is_file():
        return {}
    try:
        row = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        ts = row.get("ts")
        if ts:
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                row["_age"] = _ago(t)
                row["_stale"] = (time.time() - t) > (
                    float(row.get("interval_s") or 7200) + 600
                )
            except Exception:
                pass
        return row
    except Exception:
        return {}


def _log_mtime_ago(paths: Tuple[Path, ...]) -> str:
    newest = None
    for p in paths:
        try:
            if p.is_file():
                m = p.stat().st_mtime
                newest = m if newest is None else max(newest, m)
        except Exception:
            continue
    if newest is None:
        return "?"
    return _ago(newest)


def _tail_lines(paths: Tuple[Path, ...], n: int = 4) -> str:
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            nontrivial = [ln for ln in lines if ln.strip()]
            if not nontrivial:
                continue
            # strip ANSI for Telegram
            import re

            chunk = nontrivial[-n:]
            clean = [re.sub(r"\x1b\[[0-9;]*m", "", ln)[:90] for ln in chunk]
            return "\n".join(f"   `{ln}`" for ln in clean)
        except Exception:
            continue
    return "   _(no log yet)_"


def _emoji(st: Dict[str, object]) -> str:
    if st.get("state") == "not_installed":
        return "⚫"
    if st.get("state") == "unloaded":
        return "⚪"
    kind = st.get("kind")
    if kind == "interval":
        # armed interval job is green even when idle between ticks
        return "🟢" if st.get("armed") else "🔴"
    if st.get("running"):
        return "🟢"
    return "🔴"


def resolve_unit(arg: str) -> Optional[str]:
    a = (arg or "").strip().lower().replace("com.prospector.", "")
    if not a:
        return "com.prospector.scheduler"
    return _SHORT_TO_LABEL.get(a)


# ── Params (safe, no secrets) ───────────────────────────────────────────────

_SCHED_PLIST = PLIST_DIR / "com.prospector.scheduler.plist"
_CONFIG = REPO / "config.yaml"
_PAUSE = STORE / "PAUSE"

# Allowlisted phone-editable knobs → (kind, allowed_values)
# kind: plist_interval | plist_env | yaml_batch | yaml_cap | pause
_SAFE_PARAMS = {
    "interval": ("plist_interval", ("3600", "7200", "14400")),  # 1h / 2h / 4h
    "concurrency": ("plist_env", ("2", "4", "8")),
    "batch_size": ("yaml_batch", ("3", "5", "10")),
    "daily_cap": ("yaml_cap", ("10", "20", "40")),
}


def _read_plist_dict() -> Dict[str, object]:
    import plistlib

    if not _SCHED_PLIST.is_file():
        return {}
    with _SCHED_PLIST.open("rb") as f:
        return plistlib.load(f)


def _write_plist_dict(data: Dict[str, object]) -> None:
    import plistlib

    with _SCHED_PLIST.open("wb") as f:
        plistlib.dump(data, f, sort_keys=False)


def read_params() -> Dict[str, object]:
    """Current safe params from plist + config.yaml + PAUSE file."""
    out: Dict[str, object] = {
        "interval_s": None,
        "concurrency": None,
        "batch_size": None,
        "daily_cap_usd": None,
        "paused": _PAUSE.is_file(),
        "watchdog_interval_s": 900,
    }
    try:
        data = _read_plist_dict()
        args = [str(x) for x in (data.get("ProgramArguments") or [])]
        if "--interval" in args:
            i = args.index("--interval")
            if i + 1 < len(args):
                out["interval_s"] = int(args[i + 1])
        env = data.get("EnvironmentVariables") or {}
        if "PROSPECTOR_CURSOR_CONCURRENCY" in env:
            out["concurrency"] = int(env["PROSPECTOR_CURSOR_CONCURRENCY"])
    except Exception as exc:
        out["plist_err"] = str(exc)[:60]
    try:
        import re

        text = _CONFIG.read_text(encoding="utf-8")
        m = re.search(r"batch_size:\s*(\d+)", text)
        if m:
            out["batch_size"] = int(m.group(1))
        m = re.search(r"daily_cap_usd:\s*([0-9.]+)", text)
        if m:
            out["daily_cap_usd"] = float(m.group(1))
    except Exception as exc:
        out["config_err"] = str(exc)[:60]
    return out


def _params_lines() -> List[str]:
    p = read_params()
    iv = p.get("interval_s")
    iv_h = f"{iv // 3600}h" if isinstance(iv, int) else "?"
    pause = "ON (idle)" if p.get("paused") else "off"
    return [
        "*Params* (safe knobs — no secrets)",
        f"• interval `{iv}`s ({iv_h}) · concurrency `{p.get('concurrency')}`",
        f"• batch_size `{p.get('batch_size')}` · daily_cap `${p.get('daily_cap_usd')}`",
        f"• PAUSE file `{pause}` · watchdog every `{p.get('watchdog_interval_s')}s`",
    ]


def set_param(key: str, value: str) -> Tuple[bool, str, bool]:
    """Apply allowlisted param. Returns (ok, detail, needs_scheduler_restart)."""
    key = (key or "").strip().lower()
    value = str(value).strip()
    if key not in _SAFE_PARAMS:
        return False, f"param `{key}` not phone-editable", False
    kind, allowed = _SAFE_PARAMS[key]
    if value not in allowed:
        return False, f"`{value}` not allowed for {key} (use {', '.join(allowed)})", False

    if kind == "plist_interval":
        if not _SCHED_PLIST.is_file():
            return False, "scheduler plist missing", False
        data = _read_plist_dict()
        args = [str(x) for x in (data.get("ProgramArguments") or [])]
        if "--interval" not in args:
            return False, "plist has no --interval flag", False
        i = args.index("--interval")
        old = args[i + 1] if i + 1 < len(args) else "?"
        args[i + 1] = value
        data["ProgramArguments"] = args
        _write_plist_dict(data)
        return True, f"interval {old} → {value}s (plist)", True

    if kind == "plist_env":
        if not _SCHED_PLIST.is_file():
            return False, "scheduler plist missing", False
        data = _read_plist_dict()
        env = dict(data.get("EnvironmentVariables") or {})
        old = env.get("PROSPECTOR_CURSOR_CONCURRENCY", "?")
        env["PROSPECTOR_CURSOR_CONCURRENCY"] = value
        data["EnvironmentVariables"] = env
        _write_plist_dict(data)
        return True, f"concurrency {old} → {value} (plist env)", True

    if kind == "yaml_batch":
        import re

        text = _CONFIG.read_text(encoding="utf-8")
        new, n = re.subn(
            r"(batch_size:\s*)\d+",
            rf"\g<1>{value}",
            text,
            count=1,
        )
        if n != 1:
            return False, "could not patch batch_size in config.yaml", False
        _CONFIG.write_text(new, encoding="utf-8")
        return True, f"batch_size → {value} (config.yaml; next tick)", False

    if kind == "yaml_cap":
        import re

        text = _CONFIG.read_text(encoding="utf-8")
        new, n = re.subn(
            r"(daily_cap_usd:\s*)[0-9.]+",
            rf"\g<1>{float(value):.1f}",
            text,
            count=1,
        )
        if n != 1:
            return False, "could not patch daily_cap_usd in config.yaml", False
        _CONFIG.write_text(new, encoding="utf-8")
        return True, f"daily_cap_usd → ${value} (config.yaml; next tick)", False

    return False, "unhandled kind", False


def set_paused(paused: bool) -> Tuple[bool, str]:
    STORE.mkdir(parents=True, exist_ok=True)
    if paused:
        _PAUSE.write_text(f"paused via Telegram {datetime.utcnow().isoformat()}Z\n")
        return True, f"PAUSE armed at `{_PAUSE}` — daemon idles each cycle"
    if _PAUSE.is_file():
        _PAUSE.unlink()
        return True, "PAUSE cleared — daemon resumes next cycle"
    return True, "already unpaused"


def confirm_set_param(key: str, value: str) -> Tuple[str, List[ButtonRow]]:
    key = (key or "").strip().lower()
    value = str(value).strip()
    cur = read_params()
    labels = {
        "interval": f"daemon interval → `{value}`s (needs restart)",
        "concurrency": f"PROSPECTOR_CURSOR_CONCURRENCY → `{value}` (needs restart)",
        "batch_size": f"schedule.batch_size → `{value}` (next tick)",
        "daily_cap": f"spend.daily_cap_usd → `${value}` (next tick)",
    }
    if key not in _SAFE_PARAMS:
        return (
            f"Unknown/unsafe param `{key}`",
            [[("⚙️ Params", "estate:pd_params")]],
        )
    _, allowed = _SAFE_PARAMS[key]
    if value not in allowed:
        return (
            f"Value `{value}` not in allowlist: {', '.join(allowed)}",
            [[("⚙️ Params", "estate:pd_params")]],
        )
    text = (
        f"⚙️ *Set Prospector `{key}` = `{value}`?*\n\n"
        f"{labels.get(key, key)}\n"
        f"Current: interval `{cur.get('interval_s')}` · conc `{cur.get('concurrency')}` · "
        f"batch `{cur.get('batch_size')}` · cap `${cur.get('daily_cap_usd')}`"
    )
    return text, [
        [
            ("✅ Confirm", f"estate:pd_set_confirm:{key}:{value}"),
            ("✗ Cancel", "estate:pd_params"),
        ]
    ]


def render_params() -> Tuple[str, List[ButtonRow]]:
    lines = ["⚙️ *Prospector params*", ""] + _params_lines()
    lines.append("")
    lines.append("_Tap a value to confirm change. Secrets never shown/edited._")
    p = read_params()
    pause_btn = (
        ("▶️ Clear PAUSE", "estate:pd_unpause")
        if p.get("paused")
        else ("⏸ Arm PAUSE", "estate:pd_pause")
    )
    buttons: List[ButtonRow] = [
        [
            ("⏱ 1h", "estate:pd_set:interval:3600"),
            ("⏱ 2h", "estate:pd_set:interval:7200"),
            ("⏱ 4h", "estate:pd_set:interval:14400"),
        ],
        [
            ("⚡ conc 2", "estate:pd_set:concurrency:2"),
            ("⚡ conc 4", "estate:pd_set:concurrency:4"),
            ("⚡ conc 8", "estate:pd_set:concurrency:8"),
        ],
        [
            ("📦 batch 3", "estate:pd_set:batch_size:3"),
            ("📦 batch 5", "estate:pd_set:batch_size:5"),
            ("📦 batch 10", "estate:pd_set:batch_size:10"),
        ],
        [
            ("💵 cap $10", "estate:pd_set:daily_cap:10"),
            ("💵 cap $20", "estate:pd_set:daily_cap:20"),
            ("💵 cap $40", "estate:pd_set:daily_cap:40"),
        ],
        [pause_btn, ("🔄 Refresh", "estate:pd_params")],
        [("⚙️ Daemon", "estate:prospector_daemon"), ("🗓 Cron", "estate:pd_cron")],
    ]
    return "\n".join(lines), buttons


# ── Cron + tick outcomes ────────────────────────────────────────────────────

def _prospector_cron_jobs() -> List[Dict[str, object]]:
    path = Path.home() / ".hermes" / "cron" / "jobs.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("jobs") or []
        out = []
        for j in items:
            name = str(j.get("name") or "").lower()
            # Name-only: avoid multi-repo health scripts that merely list prospector
            if "prospector" in name:
                out.append(j)
        return out
    except Exception:
        return []


def _ago_iso(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
        return _ago(t) + " ago"
    except Exception:
        return str(iso)[:19]


def _last_ticks(n: int = 3) -> List[Dict[str, object]]:
    path = STORE / "ticks.jsonl"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        rows = []
        for ln in lines[-n:]:
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
        return rows
    except Exception:
        return []


def _cron_outcome_lines() -> List[str]:
    lines = ["*Cron + last runs*"]
    jobs = _prospector_cron_jobs()
    if not jobs:
        lines.append("• _(no Prospector-related hermes cron jobs)_")
    for j in jobs[:4]:
        name = (j.get("name") or "?")[:28]
        jid = str(j.get("id") or "?")[:8]
        en = j.get("enabled", True)
        st = j.get("last_status") or "—"
        err = j.get("last_error")
        nxt = j.get("next_run_at") or "—"
        last = _ago_iso(j.get("last_run_at"))
        mark = "🟢" if st == "ok" and en else ("⏸" if not en else "🔴")
        lines.append(f"{mark} `{jid}` *{name}* [{st}] last {last}")
        lines.append(f"   next `{str(nxt)[:22]}`")
        if err:
            lines.append(f"   ⚠ `{str(err)[:70]}`")
    ticks = _last_ticks(3)
    if ticks:
        lines.append("")
        lines.append("*Daemon ticks (latest)*")
        for t in reversed(ticks):
            ts = _ago_iso(t.get("ts"))
            if not t.get("allowed"):
                lines.append(f"🔴 skip {ts} — `{str(t.get('reason') or '')[:55]}`")
                continue
            res = t.get("result") or {}
            err = t.get("error")
            if err:
                lines.append(f"🔴 err {ts} — `{str(err)[:55]}`")
            else:
                lines.append(
                    f"🟢 {ts} · doss `{res.get('dossiers', '?')}` "
                    f"PASS `{res.get('passes', '?')}` · `{str(t.get('reason') or '')[:40]}`"
                )
    return lines


def render_cron() -> Tuple[str, List[ButtonRow]]:
    lines = ["🗓 *Prospector cron / outcomes*", ""] + _cron_outcome_lines()
    jobs = _prospector_cron_jobs()
    fail = next(
        (
            j
            for j in jobs
            if (j.get("last_status") not in (None, "ok") or j.get("last_error"))
            and j.get("enabled", True)
        ),
        None,
    )
    buttons: List[ButtonRow] = []
    if fail:
        jid = str(fail.get("id") or "")[:12]
        buttons.append(
            [
                ("▶️ Run cron job", f"estate:pd_cron_run:{jid}"),
                ("⏸ Pause cron", f"estate:pd_cron_pause:{jid}"),
            ]
        )
    else:
        # Default: prospector-daily-generation guard probe
        gen = next((j for j in jobs if "daily-generation" in str(j.get("name") or "")), None)
        if gen:
            jid = str(gen.get("id") or "")[:12]
            buttons.append(
                [
                    ("▶️ Run guard cron", f"estate:pd_cron_run:{jid}"),
                    ("📜 Logs", "estate:pd_logs:scheduler"),
                ]
            )
    buttons.append(
        [
            ("🔄 Refresh", "estate:pd_cron"),
            ("⚙️ Daemon", "estate:prospector_daemon"),
            ("⚙️ Params", "estate:pd_params"),
        ]
    )
    return "\n".join(lines), buttons


def cron_action(op: str, job_id: str) -> Tuple[bool, str]:
    """pause/run prospector-related cron via cronjob tool."""
    try:
        from tools.cronjob_tools import cronjob as cronjob_tool
        import json as _json

        action = "run" if op == "run" else "pause"
        result = _json.loads(
            cronjob_tool(
                action=action,
                job_id=job_id,
                reason="paused from Prospector phone panel" if action == "pause" else None,
            )
        )
        ok = bool(result.get("success"))
        return ok, str(result.get("error") or result.get("message") or result)[:200]
    except Exception as exc:
        return False, str(exc)[:200]


def render_prospector_daemon() -> Tuple[str, List[ButtonRow]]:
    """Full control card: daemons + params + cron/ticks + actions."""
    if not REPO.is_dir():
        return (
            "⚙️ *Prospector daemon*\n\n"
            "⚫ repo missing at `~/Documents/code/prospector` — not wired.",
            [[("🎛 Mission", "estate:refresh"), ("🚀 Fleet", "estate:fleet")]],
        )

    now_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "⚙️ *Prospector control*",
        f"_captured {now_utc} · scheduler=KeepAlive daemon · watchdog=15m oneshot_",
        "",
    ]
    any_installed = False
    for label, short, kind, role, logs in _UNITS:
        st = launchctl_state(label)
        if st.get("installed"):
            any_installed = True
        lines.append(f"{_emoji(st)} *{short}* · {st.get('detail')}")
        if st.get("state") == "not_installed":
            lines.append("   ⚠ NOT INSTALLED")
        lines.append("")

    if not any_installed:
        lines.append("_No plists — founder must install from deploy/_")
        return "\n".join(lines), [
            [("🚀 Fleet", "estate:fleet"), ("🎛 Mission", "estate:refresh")]
        ]

    lines.extend(_params_lines())
    lines.append("")
    hb = _heartbeat()
    if hb:
        stale = " · STALE" if hb.get("_stale") else ""
        hb_ts = ""
        try:
            raw_ts = hb.get("ts")
            if raw_ts:
                t = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                hb_ts = f" · `{t.strftime('%Y-%m-%d %H:%M UTC')}`"
        except Exception:
            pass
        lines.append(
            f"💓 hb `{hb.get('phase', '?')}` · `{hb.get('_age', '?')}`{stale}{hb_ts}"
        )
    lines.append("")
    lines.extend(_cron_outcome_lines())
    lines.append("")
    log_age = _log_mtime_ago(_UNITS[0][4])
    lines.append(f"*Recent log* _({log_age} ago)_")
    lines.append(_tail_lines(_UNITS[0][4], n=2))

    # CTA if last tick failed / zero pass / cron error
    ticks = _last_ticks(1)
    cta = None
    if ticks:
        t = ticks[-1]
        if t.get("error"):
            cta = ("📜 Logs", "estate:pd_logs:scheduler")
        elif not t.get("allowed") and "pause" in str(t.get("reason") or "").lower():
            cta = ("▶️ Clear PAUSE", "estate:pd_unpause")
    jobs = _prospector_cron_jobs()
    for j in jobs:
        if j.get("last_error") or (
            j.get("last_status") not in (None, "ok") and j.get("enabled", True)
        ):
            cta = ("🗓 Cron outcomes", "estate:pd_cron")
            break

    buttons: List[ButtonRow] = [
        [
            ("♻️ Restart", "estate:pd_restart:scheduler"),
            ("⏹ Stop", "estate:pd_stop:scheduler"),
            ("▶️ Start", "estate:pd_start:scheduler"),
        ],
        [
            ("⚙️ Params", "estate:pd_params"),
            ("🗓 Cron", "estate:pd_cron"),
            ("📜 Logs", "estate:pd_logs:scheduler"),
        ],
        [
            ("▶️ Run watch", "estate:pd_run_now:watchdog"),
            ("🔄 Refresh", "estate:prospector_daemon"),
            ("🚀 Fleet", "estate:fleet"),
        ],
    ]
    if cta:
        buttons.insert(0, [cta])
    return "\n".join(lines).rstrip(), buttons


def render_logs(unit_arg: str = "scheduler") -> Tuple[str, List[ButtonRow]]:
    label = resolve_unit(unit_arg) or "com.prospector.scheduler"
    unit = next((u for u in _UNITS if u[0] == label), _UNITS[0])
    short, logs = unit[1], unit[4]
    lines = [f"📜 *Prospector `{short}` logs*", ""]
    for path in logs:
        lines.append(f"*{path.name}*")
        if not path.is_file():
            lines.append("   _(missing)_")
        else:
            try:
                age = _ago(path.stat().st_mtime)
                lines.append(f"   _mtime {age} ago_")
            except Exception:
                pass
            lines.append(_tail_lines((path,), n=8))
        lines.append("")
    buttons: List[ButtonRow] = [
        [
            ("⚙️ Daemon", "estate:prospector_daemon"),
            ("🔄 Refresh logs", f"estate:pd_logs:{short}"),
        ]
    ]
    return "\n".join(lines).rstrip(), buttons


def confirm_card(op: str, unit_arg: str) -> Tuple[str, List[ButtonRow]]:
    label = resolve_unit(unit_arg)
    if not label:
        return (
            f"Unknown Prospector unit `{unit_arg}`",
            [[("⚙️ Back", "estate:prospector_daemon")]],
        )
    short = next((u[1] for u in _UNITS if u[0] == label), unit_arg)
    kind = _KIND.get(label, "keepalive")
    if not installed(label):
        return (
            f"⚫ `{short}` NOT INSTALLED (no plist).\n\n"
            f"Founder:\n"
            f"`cp {REPO}/deploy/{label}.plist ~/Library/LaunchAgents/`\n"
            f"`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist`",
            [[("⚙️ Back", "estate:prospector_daemon")]],
        )

    # Interval: run_now / stop(unload) — no fake KeepAlive start
    if kind == "interval" and op in ("start", "restart"):
        op = "run_now"
    op_word = {
        "start": "Start",
        "stop": "Unload" if kind == "interval" else "Stop",
        "restart": "Restart",
        "run_now": "Run now",
    }.get(op, op)
    warn = ""
    if label == "com.prospector.scheduler" and op in ("stop", "restart"):
        warn = "\n\nGeneration ticks pause until scheduler is running again."
    if kind == "interval" and op == "run_now":
        warn = (
            "\n\nWatchdog is a 15-min oneshot — it will exit after one check. "
            "That is normal (not a crash)."
        )
    if kind == "interval" and op == "stop":
        warn = "\n\nUnloads the interval job (no more 15-min checks until Start/load)."

    text = f"⚙️ *{op_word}* Prospector `{short}`?{warn}"
    buttons: List[ButtonRow] = [
        [
            ("✅ Confirm", f"estate:pd_{op}_confirm:{short}"),
            ("✗ Cancel", "estate:prospector_daemon"),
        ]
    ]
    return text, buttons


def _launchctl(cmd: List[str]) -> Tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    detail = ((r.stderr or r.stdout or "").strip() or "ok")[:220]
    return r.returncode == 0, detail


def run_op(op: str, unit_arg: str) -> Tuple[bool, str]:
    """Execute start/stop/restart/run_now. Returns (ok, detail) with post-state proof."""
    label = resolve_unit(unit_arg)
    if not label:
        return False, f"unknown unit `{unit_arg}`"
    if not installed(label):
        return False, f"NOT INSTALLED — no `~/Library/LaunchAgents/{label}.plist`"
    kind = _KIND.get(label, "keepalive")
    target = f"gui/{_uid()}/{label}"
    plist = PLIST_DIR / f"{label}.plist"
    before = launchctl_state(label)

    # Normalize interval ops
    if kind == "interval" and op in ("start", "restart"):
        op = "run_now"

    try:
        if op == "run_now":
            # Fire oneshot (or kickstart keepalive without kill)
            st = launchctl_state(label)
            if st.get("state") == "unloaded":
                ok, detail = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
                )
                if not ok and "already" not in detail.lower():
                    return False, detail
            ok, detail = _launchctl(["launchctl", "kickstart", target])
            if not ok and "Operation not permitted" in detail:
                # Fallback: bootout + bootstrap + kickstart
                _launchctl(["launchctl", "bootout", target])
                time.sleep(0.3)
                ok2, d2 = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
                )
                ok3, d3 = _launchctl(["launchctl", "kickstart", target])
                ok, detail = ok3, f"fallback: {d2}; {d3}"
            time.sleep(0.5)
            after = launchctl_state(label)
            if kind == "interval":
                # Success = kickstart accepted; process may already have exited
                return ok, f"{detail} · now `{after.get('detail')}` (oneshot — idle between runs is OK)"
            return ok and after.get("running"), f"{detail} · now `{after.get('detail')}`"

        if op == "restart":
            ok, detail = _launchctl(["launchctl", "kickstart", "-k", target])
            if not ok:
                # Fallback cycle for EPERM / wedged state
                _launchctl(["launchctl", "bootout", target])
                time.sleep(0.4)
                ok_b, d_b = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
                )
                ok_k, d_k = _launchctl(["launchctl", "kickstart", "-k", target])
                ok, detail = ok_k, f"fallback bootout/bootstrap: {d_b}; {d_k}"
            time.sleep(1.0)
            after = launchctl_state(label)
            pid_ok = after.get("running") and after.get("pid")
            changed = after.get("pid") != before.get("pid")
            return bool(ok and pid_ok), (
                f"{detail} · was pid {before.get('pid')} → now `{after.get('detail')}`"
                + (" · pid changed" if changed else "")
            )

        if op == "stop":
            ok, detail = _launchctl(["launchctl", "bootout", target])
            if not ok and (
                "No such process" in detail
                or "Could not find" in detail
                or "not found" in detail.lower()
            ):
                ok = True
            time.sleep(0.4)
            after = launchctl_state(label)
            stopped = after.get("state") in ("unloaded", "not running") or not after.get(
                "running"
            )
            return bool(ok and stopped), f"{detail} · now `{after.get('detail')}`"

        if op == "start":
            st = launchctl_state(label)
            if st.get("running"):
                return True, f"already running · `{st.get('detail')}`"
            if st.get("state") == "unloaded":
                ok, detail = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
                )
            else:
                ok, detail = _launchctl(["launchctl", "kickstart", target])
            if not ok and "already" in detail.lower():
                ok, detail = _launchctl(["launchctl", "kickstart", target])
            if not ok and "Operation not permitted" in detail:
                _launchctl(["launchctl", "bootout", target])
                time.sleep(0.3)
                ok, detail = _launchctl(
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)]
                )
                if ok:
                    ok2, d2 = _launchctl(["launchctl", "kickstart", target])
                    detail = f"{detail}; {d2}"
                    ok = ok2
            time.sleep(1.0)
            after = launchctl_state(label)
            return bool(ok and after.get("running")), (
                f"{detail} · now `{after.get('detail')}`"
            )

        return False, f"unknown op {op}"
    except Exception as exc:
        return False, str(exc)[:200]


def glance_line() -> str:
    """One-liner for fleet — reflects scheduler KeepAlive only."""
    st = launchctl_state("com.prospector.scheduler")
    if st.get("state") == "not_installed":
        return "Prospector daemon: NOT INSTALLED"
    hb = _heartbeat()
    age = hb.get("_age") or "?"
    phase = hb.get("phase") or "?"
    hb_ts = ""
    try:
        raw_ts = hb.get("ts")
        if raw_ts:
            t = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            hb_ts = f" · {t.strftime('%H:%M')}"
    except Exception:
        pass
    mark = "🟢" if st.get("running") and not hb.get("_stale") else "🔴"
    return f"{mark} sched `{st.get('detail')}` · hb `{phase}` {age}{hb_ts}"
