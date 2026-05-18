"""Resolve ZEDCLAW_HOME for standalone skill scripts.

Skill scripts may run outside the ZedClaw process (e.g. system Python,
nix env, CI) where ``zedclaw_constants`` is not importable.  This module
provides the same ``get_zedclaw_home()`` and ``display_zedclaw_home()``
contracts as ``zedclaw_constants`` without requiring it on ``sys.path``.

When ``zedclaw_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``zedclaw_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``ZEDCLAW_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from zedclaw_constants import display_zedclaw_home as display_zedclaw_home
    from zedclaw_constants import get_zedclaw_home as get_zedclaw_home
except (ModuleNotFoundError, ImportError):

    def get_zedclaw_home() -> Path:
        """Return the ZedClaw home directory (default: ~/.zedclaw).

        Mirrors ``zedclaw_constants.get_zedclaw_home()``."""
        val = os.environ.get("ZEDCLAW_HOME", "").strip()
        return Path(val) if val else Path.home() / ".zedclaw"

    def display_zedclaw_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``zedclaw_constants.display_zedclaw_home()``."""
        home = get_zedclaw_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
