from __future__ import annotations

import json

from oss_pr_agent import tool_registry


def test_tool_registry_enforces_lru(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEDCLAW_HOME", str(tmp_path / ".zedclaw"))
    base = tool_registry.tools_dir()
    tools = []
    for index in range(3):
        path = f"tool_{index}.sh"
        (base / path).write_text("#!/bin/sh\n", encoding="utf-8")
        tools.append({
            "name": f"tool-{index}",
            "description": "short reusable helper",
            "path": path,
            "created_at": index,
            "last_used_at": index,
        })
    tool_registry.registry_path().write_text(json.dumps({"tools": tools}), encoding="utf-8")

    kept = tool_registry.enforce_lru({"oss_pr_agent": {"max_reusable_tools": 2}})

    assert [item["name"] for item in kept] == ["tool-2", "tool-1"]
    assert (base / "tool_2.sh").exists()
    assert (base / "tool_1.sh").exists()
    assert not (base / "tool_0.sh").exists()


def test_tool_registry_prompt_lists_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEDCLAW_HOME", str(tmp_path / ".zedclaw"))
    tool_registry.save_tools([
        {
            "name": "parse-ci",
            "description": "Parse CI logs into likely fixes.",
            "path": "parse_ci.py",
            "created_at": 1,
            "last_used_at": 1,
        }
    ])

    section = tool_registry.render_prompt_section({"oss_pr_agent": {"max_reusable_tools": 15}})

    assert "parse-ci" in section
    assert "Parse CI logs" in section
    assert "registry.json" in section
