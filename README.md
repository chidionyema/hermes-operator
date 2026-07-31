# hermes-operator

The operator shell for Hermes: Telegram-driven estate ops, launchctl daemon
control, prospector generation control, and remote coding.

Extracted from `hermes-agent/gateway/operator_shell` on 2026-07-31, with history.

## Why this repo exists

The code lived in a 1.6 GB / 204k-LOC monorepo whose CI never ran on it. The
measured consequences:

| Symptom | Evidence |
|---|---|
| A red test suite shipped | commit `5aa5788607`; zero CI runs had ever executed on the branch |
| Unreviewed modules ran in production | `integrity.py` fence fired live at 08:49:16 on 2026-07-31 |
| Slow iteration | full-repo clone 1.7 GB; here it is 149 MB and the suite runs in ~2s |

The coupling that supposedly justified staying in the monorepo turned out to be
**four functions**, three of which are vendored here and one of which was
already optional:

| Borrowed symbol | Was in | Status here |
|---|---|---|
| `get_hermes_home` | `hermes_constants.py` | vendored wholesale (stdlib-only) |
| `atomic_yaml_write` | `utils.py` | vendored wholesale |
| `cronjob` | `tools/cronjob_tools.py` | optional -- degrades via `_cron_api` |
| `_load_gateway_config` | `gateway/run.py` (819 KB) | optional -- was *already* in a `try/except` |

## Host integrations (resolved at runtime, not packaged)

`coordinator`, `flight` and `learning_switch` are loaded from
`~/.hermes/scripts` by path injection at call time (`estate.py:54-93`,
`proof.py:120-125`). They are deliberately **not** dependencies: the suite
passes with no `~/.hermes` present at all.

## Test

```sh
pip install -e ".[dev]"
pytest tests/ -q          # 58 passed in ~2s
```

Verified on a clean `HOME` with no `~/.hermes` directory, which is the case CI
runs.

## Safety model

Both modules that execute anything (`daemons.py`, `prospector_daemon.py`) gate
on an allowlist before running a command:

- `_resolve_short()` / `resolve_unit()` return `None` for unknown labels, and
  the caller checks that *before* `run_op` (`estate.py` daemon routing).
- Every `subprocess.run` uses list-args; there is no `shell=True` anywhere.
- Destructive/ambiguous ops route through a confirm card first.
- `ai.hermes.gateway` start is fenced: `run_op('start', 'ai.hermes.gateway')`
  returns `(False, 'start is fenced ...')`.

`integrity.py` refuses to let the package run untracked modules -- it logs
loudly by default and denies when `HERMES_STRICT_TRACKED_IMPORTS=1`. CI fails
the build if `ghost_modules()` is non-empty.
