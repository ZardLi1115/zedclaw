"""Autonomous OSS PR agent runtime.

The runtime is intentionally separate from ZedClaw kanban: gateway only wakes
it, and real repository work is delegated to Codex CLI.
"""

