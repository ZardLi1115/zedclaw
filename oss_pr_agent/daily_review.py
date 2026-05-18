from __future__ import annotations

import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import github_ops, llm, state


DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


def _diary_dir() -> Path:
    path = state.agent_home() / "diary"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _agent_md_path() -> Path:
    return state.agent_home() / "agent.md"


def _agent_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}


def _agent_timezone(cfg: dict[str, Any]) -> ZoneInfo:
    tz_name = str(_agent_cfg(cfg).get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return DEFAULT_TZ


def _parse_hhmm(value: Any, fallback: str) -> dt_time:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dt_time(hour, minute)
    except Exception:
        pass
    hour_text, minute_text = fallback.split(":", 1)
    return dt_time(int(hour_text), int(minute_text))


def _sleep_start_datetime(cfg: dict[str, Any], now: datetime) -> datetime:
    sleep_start = _parse_hhmm(_agent_cfg(cfg).get("sleep_start"), "21:00")
    return datetime.combine(now.date(), sleep_start, tzinfo=now.tzinfo)


def read_agent_memory() -> str:
    path = _agent_md_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _day_bounds(now: datetime) -> tuple[float, float, str]:
    start = datetime.combine(now.date(), dt_time.min, tzinfo=now.tzinfo)
    end = datetime.combine(now.date(), dt_time.max, tzinfo=now.tzinfo)
    return start.timestamp(), end.timestamp(), now.date().isoformat()


def due(cfg: dict[str, Any], now_ts: float | None = None) -> bool:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
    review_at = _sleep_start_datetime(cfg, now) - timedelta(minutes=1)
    return now >= review_at


def already_ran_today(conn, cfg: dict[str, Any], now_ts: float | None = None) -> bool:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
    return state.get_meta(conn, "daily_review_last_date") == now.date().isoformat()


def consolidation_due(conn, cfg: dict[str, Any], now_ts: float | None = None) -> bool:
    agent_cfg = _agent_cfg(cfg)
    interval_days = max(1, int(agent_cfg.get("experience_consolidation_days", 3) or 3))
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
    consolidate_at = _sleep_start_datetime(cfg, now) + timedelta(hours=1)
    if now < consolidate_at:
        return False
    if state.get_meta(conn, "daily_review_last_date") != now.date().isoformat():
        return False
    last = str(state.get_meta(conn, "experience_consolidation_last_date", "") or "")
    if not last:
        return True
    try:
        last_date = datetime.fromisoformat(last).date()
    except ValueError:
        return True
    return (now.date() - last_date).days >= interval_days


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


def _collect(conn, cfg: dict[str, Any], now_ts: float | None = None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
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
    day, tasks, human_rows, merged_rows = _collect(conn, cfg, now_ts)
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
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
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


def consolidate_experience(conn, cfg: dict[str, Any], now_ts: float | None = None) -> dict[str, str]:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=_agent_timezone(cfg))
    day = now.date().isoformat()
    existing = read_agent_memory()
    diary_path = _diary_dir() / f"{day}.md"
    diary = diary_path.read_text(encoding="utf-8", errors="replace") if diary_path.exists() else ""
    agent_cfg = _agent_cfg(cfg)
    model = str(agent_cfg.get("planner_model") or agent_cfg.get("daily_review_model") or "gpt-5.5")
    summary = llm.call_text(
        model=model,
        system=(
            "你是 OSS PR Agent 的经验压缩器。输入包含旧经验记录和今天新日记。"
            "请压缩、总结、提炼为一份长期可执行经验手册，用中文 Markdown 输出。"
            "保留对后续 planner 决策和 Codex 写 PR/修 PR 最有用的规则，删除流水账、重复项和已经无用的细节。"
            "重点覆盖：选题策略、仓库筛选、CI/测试经验、PR 失败原因、避免重复犯错的方法。"
        ),
        user=(
            "# 旧经验记录\n\n"
            f"{existing or '无'}\n\n"
            "# 今天新日记\n\n"
            f"{diary or '无'}\n"
        ),
        max_tokens=int(agent_cfg.get("experience_consolidation_max_tokens", 1800) or 1800),
        temperature=0.2,
    )
    if not summary:
        summary = existing or "# OSS PR Agent 经验记录\n\n- 暂无可压缩经验。\n"
    summary = summary.strip()
    if not summary.startswith("#"):
        summary = "# OSS PR Agent 经验记录\n\n" + summary
    agent_path = _agent_md_path()
    agent_path.write_text(summary.rstrip() + "\n", encoding="utf-8")
    state.set_meta(conn, "experience_consolidation_last_date", day)
    return {
        "day": day,
        "summary": summary.rstrip() + "\n",
        "agent_path": str(agent_path),
    }
