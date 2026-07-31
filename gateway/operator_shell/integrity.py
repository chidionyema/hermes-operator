"""Refuse to run unreviewed code silently.

WHY: on 2026-07-31 ``estate.py`` imported ``gateway.operator_shell.daemons``
while ``daemons.py`` was untracked — 8,857 bytes of code holding
``launchctl bootout`` over ``ai.hermes.gateway`` and ``ai.hermes.coordinator``,
i.e. the gateway's own life support. The running process had started before the
write, so nothing was wrong *yet*; the next restart would have imported it. Git
had never seen it, no test covered it, no human had read it.

The pre-commit UNTRACKED-IMPORT GATE stops that reaching a commit. This is the
runtime half: the gateway says so in its own log.

Default is WARN, deliberately. Denying by default would take the operator panel
down at the next restart for any work-in-progress module — a self-inflicted
outage in the name of hygiene. Set ``HERMES_STRICT_TRACKED_IMPORTS=1`` to make
it fatal once the tree is clean.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent


def _tracked_modules(pkg_dir: Path) -> set:
    """Module stems git tracks in ``pkg_dir``. Empty set if git cannot answer."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", str(pkg_dir)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(pkg_dir),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("integrity: git ls-files failed (%s) — check skipped", exc)
        return set()
    if out.returncode != 0:
        logger.warning("integrity: git ls-files rc=%s — check skipped", out.returncode)
        return set()
    return {
        Path(line).stem
        for line in out.stdout.splitlines()
        if line.strip().endswith(".py")
    }


def ghost_modules(pkg_dir: Path = _PKG_DIR) -> List[str]:
    """Modules present on disk that git does not track.

    Returns [] when git cannot answer — an unavailable check must not
    manufacture a finding, and must not manufacture a clean bill of health
    either (the caller logs the skip above).
    """
    tracked = _tracked_modules(pkg_dir)
    if not tracked:
        return []
    on_disk = {
        p.stem
        for p in pkg_dir.glob("*.py")
        if p.name != "__init__.py"
    }
    return sorted(on_disk - tracked)


def enforce(pkg_dir: Path = _PKG_DIR) -> List[str]:
    """Log (or raise on) untracked modules. Returns the offenders."""
    ghosts = ghost_modules(pkg_dir)
    if not ghosts:
        return []
    detail = ", ".join(f"{g}.py" for g in ghosts)
    strict = os.environ.get("HERMES_STRICT_TRACKED_IMPORTS", "").strip() in {
        "1",
        "true",
        "yes",
    }
    message = (
        f"operator_shell is running UNREVIEWED code: {detail} "
        f"present in {pkg_dir} but untracked by git. "
        "Commit it (so it gets reviewed) or remove it."
    )
    if strict:
        raise RuntimeError(message)
    logger.error("integrity: %s", message)
    return ghosts
