"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def zedclaw_xai_user_agent() -> str:
    """Return a stable ZedClaw-specific User-Agent for xAI HTTP calls."""
    try:
        from zedclaw_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"ZedClaw-Agent/{__version__}"
