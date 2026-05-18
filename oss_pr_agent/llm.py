from __future__ import annotations

import json
import logging
import re
from typing import Any

from zedclaw_cli.config import load_config

logger = logging.getLogger(__name__)


def _json_from_text(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
    return {}


def call_json(*, model: str, system: str, user: str, max_tokens: int = 800, temperature: float = 0.1) -> dict[str, Any]:
    cfg = load_config()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    base_url = str(model_cfg.get("base_url") or "").strip()
    api_key = str(model_cfg.get("api_key") or "").strip()
    try:
        from agent.auxiliary_client import call_llm

        resp = call_llm(
            provider="custom",
            model=model,
            base_url=base_url,
            api_key=api_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=120,
        )
        content = resp.choices[0].message.content
        return _json_from_text(content)
    except Exception as exc:
        logger.warning("oss_pr_agent LLM JSON call failed: %s", exc)
        return {}


def call_text(*, model: str, system: str, user: str, max_tokens: int = 800, temperature: float = 0.1) -> str:
    cfg = load_config()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    base_url = str(model_cfg.get("base_url") or "").strip()
    api_key = str(model_cfg.get("api_key") or "").strip()
    try:
        from agent.auxiliary_client import call_llm

        resp = call_llm(
            provider="custom",
            model=model,
            base_url=base_url,
            api_key=api_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=180,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("oss_pr_agent LLM text call failed: %s", exc)
        return ""
