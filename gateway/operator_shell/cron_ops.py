"""Telegram-facing /cron operations (list / pause / resume / run / remove)."""

from __future__ import annotations

import logging
import shlex
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_USAGE = (
    "*Scheduled jobs — factory control*\n\n"
    "`/cron` — list + help\n"
    "`/cron list` — active jobs (`--all` includes paused)\n"
    "`/cron pause <id|name>`\n"
    "`/cron resume <id|name>`\n"
    "`/cron run <id|name>` — fire on next tick\n"
    "`/cron remove <id|name>`\n\n"
    "_Create/edit from CLI: `hermes cron create …` or `/cron add` in CLI._"
)


def _cron_api(**kwargs) -> Dict[str, Any]:
    from tools.cronjob_tools import cronjob as cronjob_tool
    import json

    return json.loads(cronjob_tool(**kwargs))


def cron_strip_lines(limit: int = 5) -> str:
    """Compact next-fire strip for the /panel dashboard."""
    result = _cron_api(action="list", include_disabled=False)
    if not result.get("success"):
        return ""
    jobs = result.get("jobs") or []
    if not jobs:
        return "🗓 *Cron:* no active jobs"
    lines = ["🗓 *Cron (next fires):*"]
    for job in jobs[:limit]:
        jid = str(job.get("job_id") or job.get("id") or "?")[:10]
        name = (job.get("name") or "(unnamed)")[:28]
        nxt = job.get("next_run_at") or "—"
        sched = job.get("schedule") or job.get("schedule_display") or "?"
        lines.append(f"• `{jid}` {name} — {sched} → {nxt}")
    if len(jobs) > limit:
        lines.append(f"_…+{len(jobs) - limit} more — `/cron list`_")
    return "\n".join(lines)


def _format_job_list(jobs: List[Dict[str, Any]]) -> str:
    if not jobs:
        return "No scheduled jobs. Create with `hermes cron create` or CLI `/cron add`."
    lines = ["*Scheduled Jobs*", ""]
    for job in jobs:
        jid = str(job.get("job_id") or job.get("id") or "?")
        name = job.get("name") or "(unnamed)"
        state = job.get("state") or ("scheduled" if job.get("enabled", True) else "paused")
        sched = job.get("schedule") or job.get("schedule_display") or "?"
        nxt = job.get("next_run_at") or "N/A"
        preview = (job.get("prompt_preview") or "")[:80]
        lines.append(f"• `{jid[:12]}` *{name}* [{state}]")
        lines.append(f"  {sched} → next {nxt}")
        if preview:
            lines.append(f"  _{preview}_")
        lines.append("")
    lines.append("_`/cron pause|resume|run|remove <id>`_")
    return "\n".join(lines).rstrip()


def format_cron_command(raw_args: str) -> str:
    """Parse `/cron …` args (without the leading command) and return Markdown text."""
    raw_args = (raw_args or "").strip()
    if not raw_args:
        try:
            result = _cron_api(action="list")
            jobs = result.get("jobs", []) if result.get("success") else []
        except Exception as exc:
            return f"⚠️ Cron store error:\n```text\n{exc}\n```\n\n{_USAGE}"
        body = _format_job_list(jobs)
        return f"{_USAGE}\n\n{body}"

    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        tokens = raw_args.split()

    if not tokens:
        return format_cron_command("")

    sub = tokens[0].lower()
    rest = tokens[1:]
    include_all = "--all" in rest or "-a" in rest
    positionals = [t for t in rest if not t.startswith("-")]

    try:
        if sub == "list":
            result = _cron_api(action="list", include_disabled=include_all)
            if not result.get("success"):
                return f"⚠️ Failed to list jobs: {result.get('error')}"
            return _format_job_list(result.get("jobs") or [])

        if sub in {"pause", "resume", "run", "remove", "rm", "delete"}:
            if not positionals:
                return f"Usage: `/cron {sub} <job_id|name>`"
            job_id = positionals[0]
            action = "remove" if sub in {"remove", "rm", "delete"} else sub
            result = _cron_api(
                action=action,
                job_id=job_id,
                reason="paused from Telegram /cron" if action == "pause" else None,
            )
            if not result.get("success"):
                return f"⚠️ Failed to {action}: {result.get('error')}"
            if action == "pause":
                job = result.get("job") or {}
                try:
                    from gateway.operator_shell.proof import push_undo

                    tok = push_undo(
                        "cron_pause",
                        {"cron_action": "resume", "job_id": job_id},
                        f"paused cron {job.get('name', job_id)}",
                    )
                    return (
                        f"⏸ Paused *{job.get('name', job_id)}* (`{job_id}`)\n"
                        f"· undo: `/revert {tok}`"
                    )
                except Exception:
                    return f"⏸ Paused *{job.get('name', job_id)}* (`{job_id}`)"
            if action == "resume":
                job = result.get("job") or {}
                try:
                    from gateway.operator_shell.proof import push_undo

                    tok = push_undo(
                        "cron_resume",
                        {"cron_action": "pause", "job_id": job_id},
                        f"resumed cron {job.get('name', job_id)}",
                    )
                    return (
                        f"▶️ Resumed *{job.get('name', job_id)}* (`{job_id}`)\n"
                        f"Next: {job.get('next_run_at', '—')}\n"
                        f"· undo: `/revert {tok}`"
                    )
                except Exception:
                    return (
                        f"▶️ Resumed *{job.get('name', job_id)}* (`{job_id}`)\n"
                        f"Next: {job.get('next_run_at', '—')}"
                    )
            if action == "run":
                job = result.get("job") or {}
                return (
                    f"⚡️ Triggered *{job.get('name', job_id)}* (`{job_id}`)\n"
                    "Runs on the next scheduler tick."
                )
            removed = result.get("removed_job") or {}
            return f"🗑 Removed *{removed.get('name', job_id)}* (`{job_id}`)"

        if sub in {"add", "create", "edit"}:
            return (
                f"`/cron {sub}` is CLI-only (complex flags).\n"
                "Use `hermes cron create …` in a terminal, then `/cron list` here."
            )

        return f"Unknown subcommand `{sub}`.\n\n{_USAGE}"
    except Exception as exc:
        logger.error("format_cron_command failed: %s", exc, exc_info=True)
        return f"⚠️ Cron command failed:\n```text\n{exc}\n```"
