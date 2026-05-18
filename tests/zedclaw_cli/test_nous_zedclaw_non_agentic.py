"""Tests for the Nous-ZedClaw-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"zedclaw"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``zedclaw-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "zedclaw" tag namespace.

``is_nous_zedclaw_non_agentic`` should only match the actual Nous Research
ZedClaw-3 / ZedClaw-4 chat family.
"""

from __future__ import annotations

import pytest

from zedclaw_cli.model_switch import (
    _ZEDCLAW_MODEL_WARNING,
    _check_zedclaw_model_warning,
    is_nous_zedclaw_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/ZedClaw-3-Llama-3.1-70B",
        "NousResearch/ZedClaw-3-Llama-3.1-405B",
        "zedclaw-3",
        "ZedClaw-3",
        "zedclaw-4",
        "zedclaw-4-405b",
        "zedclaw_4_70b",
        "openrouter/zedclaw3:70b",
        "openrouter/nousresearch/zedclaw-4-405b",
        "NousResearch/ZedClaw3",
        "zedclaw-3.1",
    ],
)
def test_matches_real_nous_zedclaw_chat_models(model_name: str) -> None:
    assert is_nous_zedclaw_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous ZedClaw 3/4"
    )
    assert _check_zedclaw_model_warning(model_name) == _ZEDCLAW_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "zedclaw-brain:qwen3-14b-ctx16k",
        "zedclaw-brain:qwen3-14b-ctx32k",
        "zedclaw-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat ZedClaw models we don't warn about
        "zedclaw-llm-2",
        "zedclaw2-pro",
        "nous-zedclaw-2-mistral",
        # Edge cases
        "",
        "zedclaw",  # bare "zedclaw" isn't the 3/4 family
        "zedclaw-brain",
        "brain-zedclaw-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_zedclaw_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous ZedClaw 3/4"
    )
    assert _check_zedclaw_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_zedclaw_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_zedclaw_model_warning("") == ""
