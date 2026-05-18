"""Regression tests for _apply_profile_override ZEDCLAW_HOME guard (issue #22502).

When ZEDCLAW_HOME is set to the zedclaw root (e.g. systemd hardcodes
ZEDCLAW_HOME=/root/.zedclaw), _apply_profile_override must still read
active_profile and update ZEDCLAW_HOME to the profile directory.

When ZEDCLAW_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, zedclaw_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["ZEDCLAW_HOME"] after the call,
    or None if unset.
    """
    zedclaw_root = tmp_path / ".zedclaw"
    zedclaw_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (zedclaw_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (zedclaw_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if zedclaw_home is not None:
        monkeypatch.setenv("ZEDCLAW_HOME", zedclaw_home)
    else:
        monkeypatch.delenv("ZEDCLAW_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["zedclaw", "gateway", "start"])

    from zedclaw_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("ZEDCLAW_HOME")


class TestApplyProfileOverrideZedClawHomeGuard:
    """Regression guard for issue #22502.

    Verifies that ZEDCLAW_HOME pointing to the zedclaw root does NOT suppress
    the active_profile check, while ZEDCLAW_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_zedclaw_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """ZEDCLAW_HOME=/root/.zedclaw + active_profile=coder must redirect
        ZEDCLAW_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets ZEDCLAW_HOME to the zedclaw root
        and the user switches to a profile via `zedclaw profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        zedclaw_root = tmp_path / ".zedclaw"
        zedclaw_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            zedclaw_home=str(zedclaw_root),
            active_profile="coder",
        )

        assert result is not None, "ZEDCLAW_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected ZEDCLAW_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected ZEDCLAW_HOME to end with 'coder', got: {result!r}"
        )

    def test_zedclaw_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """ZEDCLAW_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with ZEDCLAW_HOME already set to a specific profile must stay in that
        profile.
        """
        zedclaw_root = tmp_path / ".zedclaw"
        profile_dir = zedclaw_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (zedclaw_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("ZEDCLAW_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["zedclaw", "gateway", "start"])

        from zedclaw_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("ZEDCLAW_HOME") == str(profile_dir), (
            "ZEDCLAW_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_zedclaw_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: ZEDCLAW_HOME unset + active_profile=coder must set
        ZEDCLAW_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            zedclaw_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_zedclaw_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect ZEDCLAW_HOME."""
        zedclaw_root = tmp_path / ".zedclaw"
        zedclaw_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("ZEDCLAW_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["zedclaw", "gateway", "start"])
        (zedclaw_root / "active_profile").write_text("default")

        from zedclaw_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("ZEDCLAW_HOME") is None
