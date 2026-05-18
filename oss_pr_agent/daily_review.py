from __future__ import annotations

import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import github_ops, llm, state


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _diary_dir() -> Path:
    path = state.agent_home() / "diary"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _agent_md_path() -> Path:
    return state.agent_home() / "agent.md"


def _day_bounds(now: datetime) -> tuple[float, float, str]:
    start = datetime.combine(now.date(), dt_time.min, tzinfo=BEIJING_TZ)
    end = datetime.combine(now.date(), dt_time.max, tzinfo=BEIJING_TZ)
    return start.timestamp(), end.timestamp(), now.date().isoformat()


def due(now_ts: float | None = None) -> bool:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=BEIJING_TZ)
    return now.hour == 23 and now.minute >= 59


def already_ran_today(conn, now_ts: float | None = None) -> bool:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=BEIJING_TZ)
    return state.get_meta(conn, "daily_review_last_date") == now.date().isoformat()


def _pr_line(task: dict[str, Any]) -> str:
    pr_url = task.get("pr_url") or ""
    if not pr_url:
        return "未打开 PR"
    status = github_ops.pr_status(pr_url, cwd=task.get("workspace"))
    if status.get("mergedAt") or str(status.get("state") or "").upper() == "MERGED":
        return f"{pr_url}（已合并）"
    if github_ops.checks_failed(status):
        return f"{pr_url}（检查失败）"
    if github_ops.checks_passed(status):
        return f"{pr_url}（检查通过，等待维护者）"
    return f"{pr_url}（等待检查或反馈）"


def _collect(conn, now_ts: float | None = None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=BEIJING_TZ)
    start_ts, end_ts, day = _day_bounds(now)
    tasks = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM tasks
            WHERE created_at BETWEEN ? AND ? OR updated_at BETWEEN ? AND ?
            ORDER BY updated_at DESC
            """,
            (start_ts, end_ts, start_ts, end_ts),
        ).fetchall()
    ]
    human_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM human_review_items WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC",
            (start_ts, end_ts),
        ).fetchall()
    ]
    merged_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM successful_merged_pr WHERE created_at BETWEEN ? AND ? ORDER BY created_at DESC",
            (start_ts, end_ts),
        ).fetchall()
    ]
    return day, tasks, human_rows, merged_rows


def build_diary(conn, cfg: dict[str, Any], now_ts: float | None = None) -> str:
    day, tasks, human_rows, merged_rows = _collect(conn, now_ts)
    opened = [task for task in tasks if task.get("pr_url")]
    needs_fix = [task for task in tasks if int(task.get("fix_attempts") or 0) > 0]

    lines = [
        f"# {day} OSS PR Agent 日记",
        "",
        f"- 今日相关任务：{len(tasks)}",
        f"- 今日有 PR 的任务：{len(opened)}",
        f"- 今日记录合并：{len(merged_rows)}",
        f"- 今日进入人工检查：{len(human_rows)}",
        "",
        "## 今日 PR",
    ]
    if opened:
        for task in opened:
            lines.append(f"- {task.get('repo')}：{task.get('title')} -> {_pr_line(task)}")
    else:
        lines.append("- 无")

    lines.extend(["", "## 被打回/需要修复"])
    if needs_fix:
        for task in needs_fix:
            lines.append(
                f"- {task.get('repo')}：修复 {int(task.get('fix_attempts') or 0)} 轮；当前状态 {task.get('status')}；PR {task.get('pr_url') or '无'}"
            )
    else:
        lines.append("- 无明确修复轮次记录")

    lines.extend(["", "## 人工检查"])
    if human_rows:
        for item in human_rows[:20]:
            lines.append(f"- {item.get('repo') or 'unknown'} #{item.get('pr_number') or ''}：{item.get('reason')}")
            if item.get("summary"):
                lines.append(f"  - 摘要：{item.get('summary')}")
    else:
        lines.append("- 无")

    return "\n".join(lines).strip() + "\n"


def summarize_lessons(cfg: dict[str, Any], diary: str) -> str:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    model = str(agent_cfg.get("daily_review_model") or agent_cfg.get("planner_model") or "gpt-5.5")
    text = llm.call_text(
        model=model,
        system=(
            "你是 OSS PR Agent 的每日复盘器。只根据今天的日记，总结可复用经验教训。"
            "输出中文 Markdown，要求简洁、凝练、可执行。不要复述流水账。"
            "重点关注：哪些 PR 被打回、为什么、下次如何避免、哪些策略值得保留。"
        ),
        user=diary,
        max_tokens=900,
        temperature=0.2,
    )
    if text:
        return text.strip() + "\n"
    return "- LLM 总结暂时失败；保留日记供人工复盘。\n"


def run_daily_review(conn, cfg: dict[str, Any], now_ts: float | None = None) -> dict[str, str]:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=BEIJING_TZ)
    day = now.date().isoformat()
    diary = build_diary(conn, cfg, now_ts=now.timestamp())
    lessons = summarize_lessons(cfg, diary)

    diary_path = _diary_dir() / f"{day}.md"
    diary_path.write_text(diary, encoding="utf-8")

    agent_path = _agent_md_path()
    existing = agent_path.read_text(encoding="utf-8") if agent_path.exists() else "# OSS PR Agent 经验记录\n"
    section = f"## {day}\n\n{lessons.strip()}\n"
    if f"## {day}" not in existing:
        agent_path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")

    state.set_meta(conn, "daily_review_last_date", day)
    return {
        "day": day,
        "diary": diary,
        "lessons": section,
        "diary_path": str(diary_path),
        "agent_path": str(agent_path),
    }
