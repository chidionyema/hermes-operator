"""Estate control panel — mission card + one-tap ops + proof loop."""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

ButtonRow = List[Tuple[str, str]]


@dataclass
class PanelView:
    """Platform-agnostic panel payload (text + button rows)."""

    text: str
    paused: bool = False
    buttons: List[ButtonRow] = field(default_factory=list)
    toast: str = ""
    ok: bool = True
    # If set, telegram adapter should edit the pinned mission card message.
    pin_edit: bool = False
    proof_receipt: str = ""
    # Special: create cron topic (async work done by adapter)
    needs_cron_topic_setup: bool = False
    # Special: stop running agents (async — gateway runner)
    needs_stop_agent: bool = False
    # Special: run prospector with optional candidate count
    prospector_candidates: Optional[int] = None


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


_COORD_CACHE: Any = None
_COORD_ERROR: Optional[str] = None


def _load_coordinator() -> Any:
    """Import coordinator.py without permanently polluting sys.path."""
    global _COORD_CACHE, _COORD_ERROR
    if _COORD_CACHE is not None:
        return _COORD_CACHE
    if _COORD_ERROR is not None:
        raise RuntimeError(_COORD_ERROR)

    scripts = _hermes_home() / "scripts"
    coord_path = scripts / "coordinator.py"
    if not coord_path.is_file():
        _COORD_ERROR = f"Estate coordinator not found at {coord_path}"
        raise RuntimeError(_COORD_ERROR)

    scripts_str = str(scripts)
    inserted = False
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
        inserted = True
    try:
        import coordinator as C  # type: ignore

        _COORD_CACHE = C
        return C
    except Exception as exc:
        try:
            spec = importlib.util.spec_from_file_location(
                "hermes_estate_coordinator", coord_path
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not create import spec for coordinator")
            mod = importlib.util.module_from_spec(spec)
            sys.modules["hermes_estate_coordinator"] = mod
            spec.loader.exec_module(mod)
            _COORD_CACHE = mod
            return mod
        except Exception as exc2:
            _COORD_ERROR = f"Failed to load estate coordinator: {exc2 or exc}"
            if inserted:
                try:
                    sys.path.remove(scripts_str)
                except ValueError:
                    pass
            raise RuntimeError(_COORD_ERROR) from exc2


def _proof(action: str, status: str, summary: str, **kwargs) -> str:
    from gateway.operator_shell.proof import Proof, new_request_id

    p = Proof(
        action=action,
        status=status,
        summary=summary,
        request_id=kwargs.get("request_id") or new_request_id(),
        cost_usd=kwargs.get("cost_usd"),
        evidence=kwargs.get("evidence") or [],
        undoable=kwargs.get("undoable", False),
        undo_token=kwargs.get("undo_token"),
    )
    return p.render()


def render_panel_view() -> PanelView:
    """Pinned mission card dashboard."""
    try:
        from gateway.operator_shell.budget import maybe_auto_pause
        from gateway.operator_shell.mission import render_mission_card

        notice = maybe_auto_pause()
        text, paused, buttons = render_mission_card()
        if notice:
            text = notice + "\n\n" + text
        return PanelView(text=text, paused=paused, buttons=buttons, pin_edit=True)
    except Exception as exc:
        logger.error("render_panel_view failed: %s", exc, exc_info=True)
        return PanelView(
            text=(
                "⚠️ *Mission card unavailable*\n\n"
                f"```text\n{exc}\n```\n\n"
                "Gateway chat still works."
            ),
            ok=False,
            buttons=[[("🔄 Retry", "estate:refresh")]],
        )


def handle_estate_action(action: str, request_id: str = "") -> PanelView:
    """Dispatch estate:<action> with idempotency + proof receipts."""
    from gateway.operator_shell.proof import (
        check_idempotent,
        new_request_id,
        push_undo,
        store_idempotent,
        pop_undo,
    )

    raw = (action or "").strip()
    # support estate:approve:abc123
    parts = raw.split(":", 2)
    action = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        # approve:shortid form when split wrong — fix
        action = parts[0].lower()
        arg = ":".join(parts[1:])

    # normalize approve/inspect
    if action.startswith("approve"):
        # action may be "approve" and arg short id, or "approve:id" already split
        pass
    rid = request_id or new_request_id()
    prior = check_idempotent(rid)
    if prior and prior.get("text"):
        return PanelView(
            text=prior["text"],
            buttons=prior.get("buttons") or [],
            toast="Already handled",
            pin_edit=True,
            proof_receipt=prior.get("proof") or "",
        )

    try:
        C = _load_coordinator()
    except Exception as exc:
        return PanelView(
            text=f"⚠️ Estate bridge down:\n```text\n{exc}\n```",
            ok=False,
            toast="Estate unavailable",
            buttons=[[("🔄 Retry", "estate:refresh")]],
        )

    def _finish(view: PanelView) -> PanelView:
        store_idempotent(
            rid,
            {
                "text": view.text,
                "buttons": view.buttons,
                "proof": view.proof_receipt,
            },
        )
        return view

    # ---- Mission / views ----
    if action in ("refresh", "mission", ""):
        view = render_panel_view()
        view.toast = "Refreshed"
        view.proof_receipt = _proof("refresh", "done", "Mission card refreshed", request_id=rid)
        return _finish(view)

    if action == "inbox":
        from gateway.operator_shell.inbox import render_inbox

        text, buttons = render_inbox()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Inbox",
                proof_receipt=_proof("inbox", "done", "Decision inbox", request_id=rid),
            )
        )

    if action in ("rsi", "learning", "self_improve", "self-improve"):
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="RSI",
                proof_receipt=_proof(
                    "rsi", "done", "Self-improvement status", request_id=rid
                ),
            )
        )

    if action in ("brief", "sitrep", "overview"):
        from gateway.operator_shell.voice_brief import render_executive_brief

        text, buttons = render_executive_brief()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Brief",
                proof_receipt=_proof("brief", "done", "Executive brief", request_id=rid),
            )
        )

    if action in ("missions", "mission_board"):
        try:
            import flight

            conn = C.connect()
            try:
                text = flight.mission_board(conn)
            finally:
                conn.close()
        except Exception:
            text = "🚀 *Missions*\n\nBoard unavailable — try `/missions` or tap Fleet."
        buttons = [
            [
                ("🎛 Mission", "estate:refresh"),
                ("📥 Inbox", "estate:inbox"),
                ("🚀 Fleet", "estate:fleet"),
            ]
        ]
        return _finish(
            PanelView(
                text=str(text)[:3500],
                buttons=buttons,
                toast="Missions",
                proof_receipt=_proof("missions", "done", "Mission board", request_id=rid),
            )
        )

    if action in ("arm_learning", "arm"):
        try:
            import learning_switch as LS

            LS.arm("armed via Telegram estate:arm_learning")
        except Exception:
            OFF = _hermes_home() / "meta" / "OFF_SWITCH"
            OFF.parent.mkdir(parents=True, exist_ok=True)
            OFF.write_text("armed via Telegram estate:arm_learning\n")
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        receipt = _proof(
            "arm_learning", "done", "Self-improvement ARMED", request_id=rid,
            evidence=[str(_hermes_home() / "meta" / "OFF_SWITCH")],
        )
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="🟢 ARMED",
                proof_receipt=receipt,
            )
        )

    if action in ("disarm_learning", "disarm"):
        try:
            import learning_switch as LS

            LS.disarm()
        except Exception:
            OFF = _hermes_home() / "meta" / "OFF_SWITCH"
            if OFF.is_file():
                OFF.unlink()
        from gateway.operator_shell.rsi_panel import render_rsi_panel

        text, buttons = render_rsi_panel()
        receipt = _proof(
            "disarm_learning", "done", "Self-improvement DISARMED", request_id=rid
        )
        return _finish(
            PanelView(
                text=receipt + "\n\n" + text,
                buttons=buttons,
                toast="⚪ OFF",
                proof_receipt=receipt,
            )
        )

    if action == "fleet":
        from gateway.operator_shell.fleet import render_fleet

        text, buttons = render_fleet()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Fleet",
                proof_receipt=_proof("fleet", "done", "Fleet status", request_id=rid),
            )
        )

    if action in ("daemons", "daemon", "services", "launchctl"):
        from gateway.operator_shell.daemons import render_daemons

        text, buttons = render_daemons()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Daemons",
                proof_receipt=_proof("daemons", "done", "Estate daemon status", request_id=rid),
            )
        )

    if action in (
        "prospector_daemon",
        "prospector_daemons",
        "pd",
        "prospect_daemon",
    ):
        from gateway.operator_shell.prospector_daemon import render_prospector_daemon

        text, buttons = render_prospector_daemon()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Prospector daemons",
                proof_receipt=_proof(
                    "prospector_daemon", "done", "Prospector daemon status", request_id=rid
                ),
            )
        )

    if action.startswith("pd_") or action in ("pd_logs",):
        from gateway.operator_shell.prospector_daemon import (
            confirm_card as pd_confirm,
            confirm_set_param,
            cron_action as pd_cron_action,
            render_cron as pd_render_cron,
            render_logs as pd_logs,
            render_params as pd_render_params,
            render_prospector_daemon,
            run_op as pd_run,
            set_param as pd_set_param,
            set_paused as pd_set_paused,
        )

        unit = arg
        if not action.startswith("pd_"):
            pass
        else:
            rest = action[len("pd_") :]

            # Params panel
            if rest == "params":
                text, buttons = pd_render_params()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Params",
                        proof_receipt=_proof(
                            "pd_params", "done", "Prospector params", request_id=rid
                        ),
                    )
                )

            # Cron / outcomes panel
            if rest == "cron":
                text, buttons = pd_render_cron()
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Cron",
                        proof_receipt=_proof(
                            "pd_cron", "done", "Prospector cron outcomes", request_id=rid
                        ),
                    )
                )

            # Pause / unpause generation (PAUSE file)
            if rest in ("pause", "unpause"):
                ok, detail = pd_set_paused(rest == "pause")
                receipt = _proof(
                    f"pd_{rest}",
                    "done" if ok else "failed",
                    detail,
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_prospector_daemon()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("⏸ PAUSE" if rest == "pause" else "▶️ Resume"),
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Cron run/pause: pd_cron_run:id / pd_cron_pause:id
            if rest in ("cron_run", "cron_pause"):
                op = "run" if rest == "cron_run" else "pause"
                jid = unit or ""
                ok, detail = pd_cron_action(op, jid)
                receipt = _proof(
                    f"pd_cron_{op}",
                    "done" if ok else "failed",
                    f"cron {op} `{jid}`",
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = pd_render_cron()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ cron " + op) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Apply param: estate:pd_set_confirm:interval:3600 → arg=interval:3600
            if rest == "set_confirm":
                parts_kv = (unit or "").split(":", 1)
                key = parts_kv[0] if parts_kv else ""
                val = parts_kv[1] if len(parts_kv) > 1 else ""
                ok, detail, need_restart = pd_set_param(key, val)
                evidence = [detail]
                if ok and need_restart:
                    rok, rdetail = pd_run("restart", "scheduler")
                    evidence.append(f"restart: {rdetail}")
                    ok = ok and rok
                    detail = detail + " · " + rdetail
                receipt = _proof(
                    "pd_set",
                    "done" if ok else "failed",
                    f"set `{key}={val}`",
                    request_id=rid,
                    evidence=evidence,
                )
                text, buttons = pd_render_params()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ set " + key) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )

            # Confirm prompt: estate:pd_set:interval:3600 → arg=interval:3600
            if rest == "set":
                parts_kv = (unit or "").split(":", 1)
                key = parts_kv[0] if parts_kv else ""
                val = parts_kv[1] if len(parts_kv) > 1 else ""
                text, buttons = confirm_set_param(key, val)
                return _finish(PanelView(text=text, buttons=buttons, toast="Confirm set"))

            if rest.endswith("_confirm"):
                op_name = rest[: -len("_confirm")]
                ok, detail = pd_run(op_name, unit or "scheduler")
                receipt = _proof(
                    f"pd_{op_name}",
                    "done" if ok else "failed",
                    f"Prospector {op_name} `{unit or 'scheduler'}`",
                    request_id=rid,
                    evidence=[detail],
                )
                text, buttons = render_prospector_daemon()
                return _finish(
                    PanelView(
                        text=receipt + "\n\n" + text,
                        buttons=buttons,
                        toast=("✅ " + op_name) if ok else "⚠️ Failed",
                        ok=ok,
                        proof_receipt=receipt,
                    )
                )
            if rest == "logs":
                text, buttons = pd_logs(unit or "scheduler")
                return _finish(
                    PanelView(
                        text=text,
                        buttons=buttons,
                        toast="Logs",
                        proof_receipt=_proof(
                            "pd_logs", "done", f"Prospector logs `{unit}`", request_id=rid
                        ),
                    )
                )
            # confirm prompt for start/stop/restart/run_now
            text, buttons = pd_confirm(rest, unit or "scheduler")
            return _finish(PanelView(text=text, buttons=buttons, toast="Confirm"))

    if action.startswith("daemon_"):
        from gateway.operator_shell.daemons import (
            confirm_card as d_confirm,
            render_daemons,
            run_op as d_run,
            _resolve_short,
        )

        rest = action[len("daemon_") :]
        unit = arg
        if rest.endswith("_confirm"):
            op_name = rest[: -len("_confirm")]
            label = _resolve_short(unit or "")
            if not label:
                view = render_panel_view()
                view.text = f"Unknown daemon `{unit}`\n\n" + view.text
                view.ok = False
                return _finish(view)
            ok, detail = d_run(op_name, label)
            receipt = _proof(
                f"daemon_{op_name}",
                "done" if ok else "failed",
                f"{op_name} `{label}`",
                request_id=rid,
                evidence=[detail],
            )
            text, buttons = render_daemons()
            return _finish(
                PanelView(
                    text=receipt + "\n\n" + text,
                    buttons=buttons,
                    toast=("✅ " + op_name) if ok else "⚠️ Failed",
                    ok=ok,
                    proof_receipt=receipt,
                )
            )
        text, buttons = d_confirm(rest, _resolve_short(unit or "") or f"ai.hermes.{unit}")
        return _finish(PanelView(text=text, buttons=buttons, toast="Confirm"))

    if action in ("builds", "ci", "deploys", "ship"):
        from gateway.operator_shell.builds import render_builds

        text, buttons = render_builds()
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Builds",
                proof_receipt=_proof("builds", "done", "CI / deploy status", request_id=rid),
            )
        )

    if action in ("pause", "resume"):
        prev = C.estate_paused()
        new_paused = C.set_estate_paused(action == "pause")
        token = push_undo(
            action,
            {"set_paused": prev},
            f"{'paused' if new_paused else 'resumed'} spend",
        )
        view = render_panel_view()
        view.toast = "⏸ Paused" if new_paused else "▶️ Resumed"
        view.proof_receipt = _proof(
            action,
            "done",
            "Spend frozen" if new_paused else "Spend resumed",
            request_id=rid,
            undoable=True,
            undo_token=token,
            evidence=[f"flag={_hermes_home() / 'meta' / 'ESTATE_PAUSED'}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "undo":
        rec = pop_undo(arg or None)
        if not rec:
            view = render_panel_view()
            view.text = "↩ Nothing to undo.\n\n" + view.text
            view.toast = "No undo"
            return _finish(view)
        rev = rec.get("reverse") or {}
        if "set_paused" in rev:
            C.set_estate_paused(bool(rev["set_paused"]))
        elif rev.get("cron_action") == "resume" and rev.get("job_id"):
            from gateway.operator_shell.cron_ops import format_cron_command

            format_cron_command(f"resume {rev['job_id']}")
        elif rev.get("cron_action") == "pause" and rev.get("job_id"):
            from gateway.operator_shell.cron_ops import format_cron_command

            format_cron_command(f"pause {rev['job_id']}")
        view = render_panel_view()
        view.toast = "Undone"
        view.proof_receipt = _proof(
            "undo",
            "done",
            f"Reverted: {rec.get('summary')}",
            request_id=rid,
            evidence=[f"token={rec.get('token')}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "stop_agent":
        view = render_panel_view()
        view.needs_stop_agent = True
        view.toast = "Stopping…"
        view.proof_receipt = _proof(
            "stop_agent",
            "pending_confirm",
            "Stop signal issued to active agents",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "run_prospector":
        n = 20
        if arg.isdigit():
            n = max(1, min(50, int(arg)))
        view = render_panel_view()
        view.prospector_candidates = n
        view.toast = f"Prospector ×{n}"
        view.proof_receipt = _proof(
            "run_prospector",
            "done",
            f"Queued prospector generate --candidates {n}",
            request_id=rid,
            evidence=[f"workdir={Path.home() / 'Documents' / 'code' / 'prospector'}"],
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "setup_cron_topic":
        view = render_panel_view()
        view.needs_cron_topic_setup = True
        view.toast = "Cron topic…"
        view.proof_receipt = _proof(
            "setup_cron_topic",
            "pending_confirm",
            "Creating Cron topic + wiring TELEGRAM_CRON_THREAD_ID",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "budget_override":
        # Resume despite trip; mark override for the day
        from gateway.operator_shell.budget import _state_path
        import json

        C.set_estate_paused(False)
        path = _state_path()
        path.write_text(
            json.dumps({"tripped_day": "", "override_at": time.time(), "note": "manual"})
        )
        view = render_panel_view()
        view.toast = "Budget override"
        view.proof_receipt = _proof(
            "budget_override",
            "done",
            "Hard-stop overridden — spend resumed",
            request_id=rid,
        )
        view.text = view.proof_receipt + "\n\n" + view.text
        return _finish(view)

    if action == "approve" and arg:
        conn = C.connect()
        try:
            # resolve short id
            rows = C.decisions_view(conn)
            match = None
            for r in rows:
                if str(r["id"]).startswith(arg):
                    match = r
                    break
            if not match:
                text = f"⚠️ No decision matching `{arg}`"
            else:
                C.approve(conn, match["id"])
                text = _proof(
                    "approve",
                    "done",
                    f"Approved `{match['id'][:8]}` — {match['title'][:40]}",
                    request_id=rid,
                    evidence=[f"task={match['id']}"],
                )
        finally:
            conn.close()
        from gateway.operator_shell.inbox import render_inbox

        inbox_text, buttons = render_inbox()
        return _finish(
            PanelView(text=text + "\n\n" + inbox_text, buttons=buttons, toast="Approved")
        )

    if action == "inspect" and arg:
        conn = C.connect()
        try:
            rows = list(C.decisions_view(conn)) + list(C.backlog_view(conn))
            match = next((r for r in rows if str(r["id"]).startswith(arg)), None)
            if not match:
                text = f"No task `{arg}`"
            else:
                text = (
                    f"👁 `{match['id'][:12]}`\n"
                    f"*{match.get('status')}* · {match.get('risk_class', '?')}\n"
                    f"{match.get('title')}\n"
                    f"source: `{match.get('source', '')}`"
                )
        finally:
            conn.close()
        return _finish(
            PanelView(
                text=text,
                buttons=[[("📥 Inbox", "estate:inbox"), ("🎛 Mission", "estate:refresh")]],
                toast="Detail",
            )
        )

    if action == "restart":
        return _finish(
            PanelView(
                text=(
                    "♻️ *Restart coordinator?*\n\nSIGKILLs the daemon; in-flight executors "
                    "re-submit next tick. Gateway stays up."
                ),
                buttons=[
                    [
                        ("✅ Confirm", "estate:restart_confirm"),
                        ("✗ Cancel", "estate:refresh"),
                    ]
                ],
                toast="",
            )
        )

    if action == "restart_confirm":
        label = f"gui/{os.getuid()}/ai.hermes.coordinator"
        try:
            proc = subprocess.run(
                ["launchctl", "kickstart", "-k", label],
                capture_output=True,
                text=True,
                timeout=30,
            )
            ok = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "").strip()
        except Exception as exc:
            ok = False
            detail = str(exc)
        receipt = _proof(
            "restart",
            "done" if ok else "failed",
            "Coordinator relaunched" if ok else "Restart failed",
            request_id=rid,
            evidence=[detail[:200] or label],
        )
        view = render_panel_view()
        view.text = receipt + "\n\n" + view.text
        view.toast = "♻️ Restarted" if ok else "⚠️ Failed"
        view.ok = ok
        return _finish(view)

    if action == "system_fuel":
        from gateway.operator_shell.budget import check_budget

        ok, bmsg, metrics = check_budget()
        conn = C.connect()
        try:
            m = C.autonomy_ratio(conn)
            used = C.tasks_today(conn)
            msg = (
                "⛽ *Fuel*\n\n"
                f"• Tasks today: `{used}/{C.DAILY_TASK_BUDGET}`\n"
                f"• Cost (7d window fn): `${m.get('total_cost', 0):.4f}`\n"
                f"• Autonomy: `{int(m.get('autonomy_ratio', 0)*100)}%`\n"
                f"• Budget: {'OK' if ok else 'TRIPPED'} — {bmsg}\n"
            )
            if metrics:
                msg += f"• Hard ceiling: `{metrics.get('max_tasks_per_day')} tasks` / `${metrics.get('max_usd_per_day'):.2f}`\n"
        finally:
            conn.close()
        import os as _os
        ntfy = (_os.getenv("NTFY_TOPIC") or "").strip()
        if ntfy:
            msg += f"• NTFY: `{ntfy}` (dual-path P0 on)\n"
        else:
            msg += (
                "• NTFY: unset — optional P0 backup. "
                "Set `NTFY_TOPIC=your-private-topic` in ~/.hermes/.env "
                "(+ `OPERATOR_SHELL_ALWAYS_NTFY=1` to always fan out).\n"
            )
        buttons = [[("🎛 Mission", "estate:refresh")]]
        if not ok:
            buttons.insert(0, [("🔓 Override budget", "estate:budget_override")])
        return _finish(
            PanelView(
                text=msg + "\n" + _proof("fuel", "done", bmsg, request_id=rid),
                buttons=buttons,
                toast="Fuel",
            )
        )

    if action == "list_active":
        conn = C.connect()
        try:
            active = C.list_active(conn)
            if not active:
                msg = "🗂️ No active tasks."
            else:
                lines = ["🗂️ *Active:*"]
                for t in active[:12]:
                    lines.append(f"• `{t['id'][:8]}` [{t['status']}] {t['title'][:40]}")
                msg = "\n".join(lines)
        finally:
            conn.close()
        return _finish(
            PanelView(
                text=msg,
                buttons=[[("🎛 Mission", "estate:refresh")]],
                toast="Active",
            )
        )

    if action == "view_logs":
        log_path = _hermes_home() / "logs" / "coordinator.log"
        if log_path.is_file():
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-15:]
            msg = f"🪵 *Logs:*\n```text\n{''.join(lines)[-3000:]}\n```"
        else:
            msg = "⚠️ Log file missing."
        return _finish(
            PanelView(
                text=msg,
                buttons=[[("🎛 Mission", "estate:refresh")]],
                toast="Logs",
            )
        )

    if action == "cron_strip":
        from gateway.operator_shell.cron_ops import format_cron_command

        return _finish(
            PanelView(
                text=format_cron_command("list"),
                buttons=[[("🎛 Mission", "estate:refresh")]],
                toast="Cron",
            )
        )

    if action == "mute_progress":
        from gateway.operator_shell.delivery import cycle_telegram_tool_progress

        mode = cycle_telegram_tool_progress()
        view = render_panel_view()
        view.text = (
            _proof("mute", "done", f"Telegram progress → {mode}", request_id=rid)
            + "\n\n"
            + view.text
        )
        view.toast = f"Progress: {mode}"
        return _finish(view)

    # ---- Claude Code remote (task cards / cancel / pause / steer) ----
    if action == "task" and arg:
        from gateway.operator_shell.code_remote import render_task_card

        text, buttons = render_task_card(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Task",
                proof_receipt=_proof("task", "done", f"Task `{arg}`", request_id=rid),
            )
        )

    if action == "cancel" and arg:
        from gateway.operator_shell.code_remote import cancel_task

        text, buttons = cancel_task(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Cancelled",
                proof_receipt=_proof(
                    "cancel", "done", f"Cancelled `{arg}`", request_id=rid
                ),
            )
        )

    if action == "pause_task" and arg:
        from gateway.operator_shell.code_remote import pause_task

        text, buttons = pause_task(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Paused",
                proof_receipt=_proof(
                    "pause_task", "done", f"Paused `{arg}`", request_id=rid
                ),
            )
        )

    if action == "steer_prompt" and arg:
        from gateway.operator_shell.code_remote import steer_prompt_card

        text, buttons = steer_prompt_card(arg)
        return _finish(
            PanelView(
                text=text,
                buttons=buttons,
                toast="Steer",
                proof_receipt=_proof(
                    "steer_prompt", "done", f"Steer help `{arg}`", request_id=rid
                ),
            )
        )

    view = render_panel_view()
    view.text = f"⚠️ Unknown action `{action}`\n\n" + view.text
    view.toast = "Unknown"
    view.ok = False
    return _finish(view)
