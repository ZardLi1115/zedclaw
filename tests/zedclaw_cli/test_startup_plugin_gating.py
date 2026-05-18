"""Guards for CLI startup performance regression.

``zedclaw_cli.main`` skips eager plugin discovery at argparse-setup time
when the invocation is clearly targeting a known built-in subcommand.
This saves 500-650ms on ``zedclaw --help``, ``zedclaw version``,
``zedclaw logs``, etc., by not importing ``google.cloud.pubsub_v1``,
``aiohttp``, ``grpc``, and friends.

Two invariants:

1. ``_BUILTIN_SUBCOMMANDS`` must contain every subcommand that is actually
   registered by ``main()``.  If an entry is missing, plugin discovery
   runs unnecessarily for that command (correctness-safe, just slow).
   If an entry is PRESENT but the subcommand doesn't exist, a plugin
   could shadow the name — also bad.

2. ``_plugin_cli_discovery_needed()`` returns the right answer for the
   flag/positional parsing cases it's meant to handle.
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from zedclaw_cli.main import (
    _BUILTIN_SUBCOMMANDS,
    _first_positional_argv,
    _plugin_cli_discovery_needed,
)


# ── helper: grab the live set of top-level subcommands from argparse ───────


def _live_subcommand_names() -> set[str]:
    """Run ``zedclaw --help`` in-process and parse the subcommand block.

    We patch ``_plugin_cli_discovery_needed`` to always return False so
    plugin-registered commands aren't included — we're validating the
    built-in-only set.
    """
    from zedclaw_cli import main as _main

    argv_backup = sys.argv[:]
    sys.argv = ["zedclaw", "--help"]
    buf = io.StringIO()
    try:
        with patch.object(_main, "_plugin_cli_discovery_needed", return_value=False):
            with redirect_stdout(buf):
                with pytest.raises(SystemExit):
                    _main.main()
    finally:
        sys.argv = argv_backup

    text = buf.getvalue()
    # argparse prints "{chat,model,...}" somewhere in the help output
    m = re.search(r"\{([a-zA-Z0-9_,\-]+)\}", text)
    assert m, f"Could not find subcommand group in --help output:\n{text[:500]}"
    return set(m.group(1).split(","))


# ── _first_positional_argv ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["zedclaw"], None),
        (["zedclaw", "--help"], None),
        (["zedclaw", "-h"], None),
        (["zedclaw", "--version"], None),
        (["zedclaw", "-w"], None),
        # -p / --profile is stripped from sys.argv by
        # _apply_profile_override() at import time, so it never reaches
        # _first_positional_argv. We test with just -w / --tui here.
        (["zedclaw", "-w", "--tui"], None),
        (["zedclaw", "version"], "version"),
        (["zedclaw", "--tui", "chat"], "chat"),
        (["zedclaw", "-w", "logs"], "logs"),
        (["zedclaw", "chat", "hello world"], "chat"),
        (["zedclaw", "gateway", "run"], "gateway"),
        # Top-level value-taking flags: the value should be skipped.
        (["zedclaw", "-m", "gpt5", "chat"], "chat"),
        (["zedclaw", "--model", "gpt5", "chat", "hi"], "chat"),
        (["zedclaw", "-m", "gpt5", "--provider", "openai", "chat"], "chat"),
        (["zedclaw", "-z", "hello world"], None),
        (["zedclaw", "-z", "hello", "chat"], "chat"),
        (["zedclaw", "--model=gpt5", "chat"], "chat"),     # inline form
        (["zedclaw", "--", "chat"], "chat"),               # -- terminator
        (["zedclaw", "-w", "--"], None),
        # Unknown positional after skipped flags → plugin-cmd candidate.
        (["zedclaw", "some-plugin-cmd"], "some-plugin-cmd"),
        (["zedclaw", "-m", "gpt5", "some-plugin-cmd"], "some-plugin-cmd"),
    ],
)
def test_first_positional_argv(argv, expected):
    with patch.object(sys, "argv", argv):
        assert _first_positional_argv() == expected


# ── _plugin_cli_discovery_needed ───────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["zedclaw"],                          # bare → chat
        ["zedclaw", "--help"],                # top-level help
        ["zedclaw", "-h"],
        ["zedclaw", "version"],               # known built-in
        ["zedclaw", "logs"],
        ["zedclaw", "gateway", "run"],
        ["zedclaw", "--tui"],
        ["zedclaw", "-w", "--tui"],
        ["zedclaw", "chat", "hi"],
        ["zedclaw", "help"],                  # accepted built-in-ish
        ["zedclaw", "-m", "gpt5", "chat"],    # flag-value-skipping
    ],
)
def test_discovery_skipped_for_builtins(argv):
    with patch.object(sys, "argv", argv):
        assert _plugin_cli_discovery_needed() is False


@pytest.mark.parametrize(
    "argv",
    [
        ["zedclaw", "meet", "join"],          # potential google_meet plugin
        ["zedclaw", "honcho", "status"],      # potential memory plugin
        ["zedclaw", "unknown-subcmd"],
    ],
)
def test_discovery_runs_for_unknown_positional(argv):
    with patch.object(sys, "argv", argv):
        assert _plugin_cli_discovery_needed() is True


# ── _BUILTIN_SUBCOMMANDS ↔ argparse registration parity ────────────────────


def test_builtin_set_covers_every_registered_subcommand():
    """Every subcommand registered in main() must appear in the set.

    Missing entries cause a slow-path regression (correctness stays
    fine — discovery just runs unnecessarily).
    """
    live = _live_subcommand_names()
    # "help" is synthetic — an argparse-implicit convenience we include
    # in the set so ``zedclaw help <cmd>`` skips discovery; it won't show
    # up as a subparser in the --help output.
    declared = _BUILTIN_SUBCOMMANDS - {"help"}
    missing_from_declaration = live - declared
    assert not missing_from_declaration, (
        f"_BUILTIN_SUBCOMMANDS is missing these live subcommands: "
        f"{sorted(missing_from_declaration)}. Add them to "
        f"zedclaw_cli/main.py::_BUILTIN_SUBCOMMANDS so plugin discovery "
        f"can be skipped when the user targets them."
    )


def test_builtin_set_has_no_phantom_entries():
    """No entry in the set should refer to a subcommand that no longer exists.

    A phantom entry means plugin discovery gets incorrectly skipped for
    a name that — if a plugin actually registered it — would fail to
    parse. Keeps the set honest.
    """
    live = _live_subcommand_names()
    allowed_synthetic = {"help"}
    phantom = _BUILTIN_SUBCOMMANDS - live - allowed_synthetic
    assert not phantom, (
        f"_BUILTIN_SUBCOMMANDS has entries that are not registered as "
        f"top-level subparsers: {sorted(phantom)}"
    )
