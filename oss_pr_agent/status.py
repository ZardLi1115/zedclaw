from __future__ import annotations

import fcntl
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from zedclaw_cli.config import load_config

from . import github_ops, state


ACTIVE_STATUSES = ("RUNNING_CODEX", "OPENING_PR", "WAITING_CI", "NEEDS_FIX", "FIXING_PR")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _age(ts: float | int | None) -> str:
    if not ts:
        return "unknown"
    seconds = max(0, int(time.time() - float(ts)))
    if seconds < 90:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _iso(ts: float | int | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(BEIJING_TZ).strftime("%H:%M")


def _lock_state() -> str:
    lock_path = state.agent_home() / ".runtime.lock"
    try:
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                return "idle"
            except BlockingIOError:
                return "working"
    except Exception as exc:
        return f"unknown ({exc})"


def _codex_processes() -> list[str]:
    try:
        proc = subprocess.run(
            ["ps", "-ef"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    lines = []
    for line in proc.stdout.splitlines():
        if "codex exec" in line and "oss_pr_agent/workspaces" in line:
            lines.append(line.strip())
    return lines[:3]


def _latest_run(conn, task_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM runs WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def _pr_status(task: dict[str, Any]) -> str:
    pr_url = task.get("pr_url")
    if not pr_url:
        return "not opened"
    status = github_ops.pr_status(pr_url, cwd=task.get("workspace"))
    if not status:
        return "unknown"
    state_text = str(status.get("state") or "unknown")
    if status.get("mergedAt") or state_text.upper() == "MERGED":
        return f"merged at {status.get('mergedAt') or 'unknown'}"
    if github_ops.checks_failed(status):
        return f"{state_text}, checks failing"
    if github_ops.checks_passed(status):
        return f"{state_text}, checks passing"
    return f"{state_text}, checks pending/unknown"


def _phase(task: dict[str, Any]) -> str:
    status_text = str(task.get("status") or "UNKNOWN")
    attempts = int(task.get("fix_attempts") or 0)
    if status_text == "RUNNING_CODEX":
        return "first PR draft"
    if status_text == "FIXING_PR":
        return f"fix attempt {attempts}"
    if status_text == "NEEDS_FIX":
        return f"queued for fix attempt {attempts + 1}"
    if status_text == "WAITING_CI":
        return "waiting for CI/review" if attempts == 0 else f"waiting after fix attempt {attempts}"
    if status_text == "PR_READY":
        return "ready for maintainer review"
    if status_text == "FAILED_NEEDS_HUMAN":
        return "needs human review"
    return status_text.lower()


def _zh_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if (
        "existing" in lower
        and "pr" in lower
        and "waiting on ci" in lower
        and "candidate" in lower
        and ("capacity" in lower or "available active pr slots" in lower)
    ):
        return "现有 PR 正在等待 CI/维护者反馈，当前没有需要修复的失败；活跃 PR 名额仍可用，planner 认为有候选任务达到评分阈值，可以继续开新 PR。"
    if "existing" in lower and "pr" in lower and "waiting on ci" in lower and "nothing to fix" in lower:
        return "现有 PR 正在等待 CI/维护者反馈，暂时没有需要修复的内容。"
    if "github cli is not authenticated" in lower:
        return "GitHub CLI 尚未认证。"
    if "check active prs" in lower:
        return "检查活跃 PR。"
    if "check newly opened pr" in lower:
        return "检查新打开的 PR。"
    if "check pr after" in lower and "fix" in lower:
        return "自动修复后检查 PR。"
    replacements = [
        ("existing pr", "现有 PR"),
        ("existing active pr", "现有活跃 PR"),
        ("is waiting on ci", "正在等待 CI"),
        ("with no reported failure to fix", "目前没有报告需要修复的失败"),
        ("there are available active pr slots", "还有可用的活跃 PR 名额"),
        ("active pr capacity is available", "活跃 PR 容量仍可用"),
        ("candidate", "候选任务"),
        ("meets the minimum score threshold", "达到最低评分阈值"),
        ("meets the score threshold", "达到评分阈值"),
        ("while avoiding duplicating the already-active issue", "且避免重复处理已有活跃 issue"),
        ("while not duplicating the active pr", "且没有重复当前活跃 PR"),
        ("no reported failure", "没有报告失败"),
        ("nothing to fix yet", "暂时没有需要修复的内容"),
        ("check active prs", "检查活跃 PR"),
        ("check pr after automated fix", "自动修复后检查 PR"),
        ("check pr after email-triggered fix", "邮件/GitHub 触发修复后检查 PR"),
        ("check newly opened pr", "检查新打开的 PR"),
        ("planner sleep", "planner 休眠"),
        ("manual wake after action case fix", "修复 action 大小写后手动唤醒"),
        ("manual wake after action alias fix", "修复 action 别名后手动唤醒"),
    ]
    out = text
    for src, dst in replacements:
        out = out.replace(src, dst).replace(src.capitalize(), dst)
    if out != text:
        return out
    return f"Runtime 计划原因：{text}"


def get_language() -> str:
    with state.connect() as conn:
        lang = str(state.get_meta(conn, "osspr_language", "") or "").strip().lower()
    if not lang:
        cfg = load_config()
        agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
        lang = str(agent_cfg.get("language") or "en").strip().lower()
    return "zh" if lang in {"zh", "cn", "chinese", "中文"} else "en"


def set_language(lang: str) -> str:
    value = str(lang or "").strip().lower()
    if value in {"zh", "cn", "chinese", "中文"}:
        normalized = "zh"
    elif value in {"en", "english"}:
        normalized = "en"
    else:
        raise ValueError("Unsupported language. Use zh or en.")
    with state.connect() as conn:
        state.set_meta(conn, "osspr_language", normalized)
    return normalized


def render_human_review() -> str:
    lang = get_language()
    with state.connect() as conn:
        state.cleanup_human_review_items(conn)
        state.write_human_review_markdown(conn)
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM human_review_items WHERE status='open' ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        ]
    if lang == "zh":
        if not rows:
            return "当前没有真实人工待办。"
        lines = [f"当前真实人工待办：{len(rows)} 项"]
        for index, item in enumerate(rows, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. {item.get('repo') or 'unknown'} #{item.get('pr_number') or ''}".rstrip(),
                    f"   标题：{item.get('subject') or '无'}",
                    f"   原因：{item.get('reason') or '无'}",
                    f"   摘要：{(item.get('summary') or '无')[:700]}",
                ]
            )
        return "\n".join(lines)

    if not rows:
        return "No real human review items are currently open."
    lines = [f"Real human review items: {len(rows)}"]
    for index, item in enumerate(rows, start=1):
        lines.extend(
            [
                "",
                f"{index}. {item.get('repo') or 'unknown'} #{item.get('pr_number') or ''}".rstrip(),
                f"   Subject: {item.get('subject') or '(none)'}",
                f"   Reason: {item.get('reason') or '(none)'}",
                f"   Summary: {(item.get('summary') or '(none)')[:700]}",
            ]
        )
    return "\n".join(lines)


def render_status() -> str:
    lang = get_language()
    with state.connect() as conn:
        lock = _lock_state()
        next_wake = float(state.get_meta(conn, "next_wake_at", 0) or 0)
        next_reason = state.get_meta(conn, "next_wake_reason", "")
        last_email = float(state.get_meta(conn, "last_email_check_at", 0) or 0)
        active_rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN (?,?,?,?,?) ORDER BY updated_at DESC",
            ACTIVE_STATUSES,
        ).fetchall()
        active = [dict(row) for row in active_rows]
        last_task_row = conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        human_count = conn.execute(
            "SELECT count(*) AS c FROM human_review_items WHERE status='open'"
        ).fetchone()["c"]
        submitted_count = conn.execute(
            "SELECT count(*) AS c FROM tasks WHERE pr_url IS NOT NULL AND pr_url != ''"
        ).fetchone()["c"]
        merged_count = conn.execute(
            "SELECT count(*) AS c FROM successful_merged_pr"
        ).fetchone()["c"]

        now = time.time()
        if lock == "working":
            state_word = "working"
        elif active:
            state_word = "monitoring"
        elif next_wake and now < next_wake:
            state_word = "sleeping"
        else:
            state_word = "ready"

        if lang == "zh":
            state_map = {
                "working": "工作中",
                "monitoring": "监控中",
                "sleeping": "休眠中",
                "ready": "就绪",
                "idle": "空闲",
            }
            lines = [
                f"OSS PR Agent：{state_map.get(state_word, state_word)}",
                f"Runtime 锁：{state_map.get(lock, lock)}",
                f"下次唤醒（北京时间）：{_iso(next_wake)}" + (f"（{_zh_reason(next_reason)}）" if next_reason else ""),
                f"上次 Gmail 检查：{_age(last_email)}",
                f"待人工检查项：{human_count}",
                f"已提交 PR 数：{submitted_count}",
                f"已记录合并 PR：{merged_count}",
            ]
        else:
            lines = [
                f"OSS PR Agent: {state_word}",
                f"Runtime lock: {lock}",
                f"Next wake (Beijing): {_iso(next_wake)}" + (f" ({next_reason})" if next_reason else ""),
                f"Last Gmail check: {_age(last_email)}",
                f"Open human review items: {human_count}",
                f"Submitted PRs: {submitted_count}",
                f"Merged PRs tracked: {merged_count}",
            ]


        codex = _codex_processes()
        if codex:
            lines.append(f"Codex 进程：运行中（{len(codex)}）" if lang == "zh" else f"Codex process: running ({len(codex)})")

        tasks = active or ([dict(last_task_row)] if last_task_row else [])
        if not tasks:
            lines.append("任务：暂无" if lang == "zh" else "Task: none yet")
            return "\n".join(lines)

        lines.append("")
        lines.append("当前任务：" if lang == "zh" else "Current task:")
        for task in tasks[:3]:
            run = _latest_run(conn, task["id"])
            if lang == "zh":
                lines.extend(
                    [
                        f"- 仓库：{task.get('repo')}",
                        f"  标题：{task.get('title')}",
                        f"  Runtime 状态：{task.get('status')}（{_phase(task)}）",
                        f"  PR：{task.get('pr_url') or '无'}",
                        f"  PR 状态：{_pr_status(task)}",
                        f"  修复轮次：{int(task.get('fix_attempts') or 0)}/{int(task.get('max_fix_attempts') or 5)}",
                        f"  更新时间：{_age(task.get('updated_at'))}",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"- Repo: {task.get('repo')}",
                        f"  Title: {task.get('title')}",
                        f"  Runtime status: {task.get('status')} ({_phase(task)})",
                        f"  PR: {task.get('pr_url') or '(none)'}",
                        f"  PR status: {_pr_status(task)}",
                        f"  Fix attempts: {int(task.get('fix_attempts') or 0)}/{int(task.get('max_fix_attempts') or 5)}",
                        f"  Updated: {_age(task.get('updated_at'))}",
                    ]
                )
            if run:
                prefix = "  最近运行：" if lang == "zh" else "  Latest run: "
                lines.append(f"{prefix}{run.get('kind')} {run.get('status')} started {_age(run.get('started_at'))}")
                if run.get("error"):
                    lines.append(f"  运行错误：{run.get('error')}" if lang == "zh" else f"  Run error: {run.get('error')}")
            if task.get("failure_reason"):
                reason = str(task.get("failure_reason"))
                lines.append(f"  失败原因：{reason[:500]}" if lang == "zh" else f"  Failure: {reason[:500]}")

        return "\n".join(lines)
