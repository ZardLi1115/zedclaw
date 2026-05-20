from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import state


def tools_dir() -> Path:
    path = state.agent_home() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_path() -> Path:
    return tools_dir() / "registry.json"


def _max_tools(cfg: dict[str, Any]) -> int:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    try:
        return max(0, int(agent_cfg.get("max_reusable_tools", 15) or 15))
    except Exception:
        return 15


def _normalize_tool(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()[:80]
    description = " ".join(str(item.get("description") or "").split())[:180]
    path = str(item.get("path") or "").strip()
    if not name or not description or not path:
        return None
    raw_last_used = item.get("last_used_at")
    if raw_last_used is None:
        raw_last_used = item.get("created_at")
    try:
        last_used_at = float(raw_last_used if raw_last_used is not None else 0)
    except Exception:
        last_used_at = 0.0
    raw_created = item.get("created_at")
    try:
        created_at = float(raw_created if raw_created is not None else (last_used_at or time.time()))
    except Exception:
        created_at = time.time()
    return {
        "name": name,
        "description": description,
        "path": path,
        "created_at": created_at,
        "last_used_at": last_used_at or created_at,
    }


def load_tools(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        raw = json.loads(registry_path().read_text(encoding="utf-8"))
    except Exception:
        return []
    items = raw.get("tools") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    normalized = [_normalize_tool(item) for item in items]
    tools = [item for item in normalized if item is not None]
    tools.sort(key=lambda item: float(item.get("last_used_at") or 0), reverse=True)
    if cfg is not None:
        tools = tools[: _max_tools(cfg)]
    return tools


def save_tools(tools: list[dict[str, Any]]) -> None:
    path = registry_path()
    path.write_text(
        json.dumps({"tools": tools}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def enforce_lru(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    max_tools = _max_tools(cfg)
    tools = load_tools()
    if max_tools <= 0:
        save_tools([])
        return []
    kept = tools[:max_tools]
    kept_paths = {str(item.get("path") or "") for item in kept}
    for item in tools[max_tools:]:
        rel_path = str(item.get("path") or "")
        if rel_path in kept_paths:
            continue
        candidate = (tools_dir() / rel_path).resolve()
        try:
            candidate.relative_to(tools_dir().resolve())
        except ValueError:
            continue
        if candidate.is_file():
            try:
                candidate.unlink()
            except OSError:
                pass
    save_tools(kept)
    return kept


def render_prompt_section(cfg: dict[str, Any]) -> str:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    if not agent_cfg.get("reusable_tools_enabled", True):
        return ""
    tools = enforce_lru(cfg)
    max_tools = _max_tools(cfg)
    registry = registry_path()
    base = tools_dir()
    lines = [
        "# Reusable Local Tools",
        "",
        f"Tool directory: `{base}`",
        f"Registry: `{registry}`",
        f"Maximum registered tools: {max_tools}",
        "",
        "Available tools:",
    ]
    if tools:
        for item in tools:
            lines.append(f"- {item['name']}: {item['description']} (`{base / item['path']}`)")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "When you create a generally reusable helper, save it under the tool directory and register it in registry.json.",
            "Keep descriptions short. When you use a tool, update its `last_used_at` Unix timestamp.",
            "Do not store secrets in tools or registry entries.",
        ]
    )
    return "\n".join(lines).strip()
