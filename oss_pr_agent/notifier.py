from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _enabled(cfg: dict[str, Any]) -> bool:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    value = agent_cfg.get("notify_enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def target(cfg: dict[str, Any]) -> str:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    return str(agent_cfg.get("notify_target") or "feishu").strip() or "feishu"


def _language() -> str:
    try:
        from oss_pr_agent.status import get_language

        return get_language()
    except Exception:
        return "en"


def send(cfg: dict[str, Any], message: str) -> bool:
    if not _enabled(cfg):
        return False
    try:
        try:
            from zedclaw_cli.env_loader import load_zedclaw_dotenv
            from zedclaw_constants import get_zedclaw_home

            load_zedclaw_dotenv(zedclaw_home=get_zedclaw_home())
        except Exception:
            pass

        from tools.send_message_tool import send_message_tool

        raw = send_message_tool({"action": "send", "target": target(cfg), "message": message})
        try:
            result = json.loads(raw)
        except Exception:
            result = {"raw": raw}
        if isinstance(result, dict) and result.get("error"):
            logger.warning("oss_pr_agent notification failed: %s", result.get("error"))
            return False
        logger.info("oss_pr_agent notification sent to %s", target(cfg))
        return True
    except Exception as exc:
        logger.warning("oss_pr_agent notification failed: %s", exc)
        return False


def notify_pr_submitted(cfg: dict[str, Any], task: dict[str, Any]) -> None:
    if _language() == "zh":
        lines = [
            "OSS PR Agent 已提交 PR。",
            f"仓库：{task.get('repo')}",
            f"Issue：{task.get('issue_url') or task.get('issue_number')}",
            f"PR：{task.get('pr_url')}",
            f"任务：{task.get('id')}",
        ]
    else:
        lines = [
            "OSS PR Agent submitted a PR.",
            f"Repo: {task.get('repo')}",
            f"Issue: {task.get('issue_url') or task.get('issue_number')}",
            f"PR: {task.get('pr_url')}",
            f"Task: {task.get('id')}",
        ]
    send(
        cfg,
        "\n".join(lines),
    )


def notify_task_started(cfg: dict[str, Any], task: dict[str, Any]) -> None:
    if _language() == "zh":
        lines = [
            "OSS PR Agent 开始处理新任务。",
            f"仓库：{task.get('repo')}",
            f"Issue：{task.get('issue_url') or task.get('issue_number')}",
            f"任务：{task.get('id')}",
            f"标题：{task.get('title')}",
        ]
    else:
        lines = [
            "OSS PR Agent started a new task.",
            f"Repo: {task.get('repo')}",
            f"Issue: {task.get('issue_url') or task.get('issue_number')}",
            f"Task: {task.get('id')}",
            f"Title: {task.get('title')}",
        ]
    send(cfg, "\n".join(lines))


def notify_status_changed(cfg: dict[str, Any], task: dict[str, Any], old_status: str, new_status: str, detail: str = "") -> None:
    if old_status == new_status:
        return
    if _language() == "zh":
        lines = [
            "OSS PR Agent 任务状态已变化。",
            f"仓库：{task.get('repo')}",
            f"PR：{task.get('pr_url') or '无'}",
            f"任务：{task.get('id')}",
            f"状态：{old_status} -> {new_status}",
        ]
        if detail:
            lines.append(f"详情：{detail}")
    else:
        lines = [
            "OSS PR Agent task status changed.",
            f"Repo: {task.get('repo')}",
            f"PR: {task.get('pr_url') or '(none)'}",
            f"Task: {task.get('id')}",
            f"Status: {old_status} -> {new_status}",
        ]
        if detail:
            lines.append(f"Detail: {detail}")
    send(cfg, "\n".join(lines))


def notify_pr_resubmitted(cfg: dict[str, Any], task: dict[str, Any], attempt: int) -> None:
    if _language() == "zh":
        lines = [
            "OSS PR Agent 已根据 review/CI 反馈更新 PR。",
            f"仓库：{task.get('repo')}",
            f"Issue：{task.get('issue_url') or task.get('issue_number')}",
            f"PR：{task.get('pr_url')}",
            f"任务：{task.get('id')}",
            f"修复轮次：{attempt}",
        ]
    else:
        lines = [
            "OSS PR Agent updated a PR after review/CI feedback.",
            f"Repo: {task.get('repo')}",
            f"Issue: {task.get('issue_url') or task.get('issue_number')}",
            f"PR: {task.get('pr_url')}",
            f"Task: {task.get('id')}",
            f"Fix attempt: {attempt}",
        ]
    send(
        cfg,
        "\n".join(lines),
    )


def _send_long(cfg: dict[str, Any], header: str, body: str, chunk_size: int = 3000) -> None:
    text = body or ""
    if not text:
        send(cfg, header)
        return
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        suffix = f"（{index}/{total}）" if _language() == "zh" else f" ({index}/{total})"
        send(cfg, f"{header}{suffix}\n\n{chunk}")


def notify_daily_review(cfg: dict[str, Any], review: dict[str, str]) -> None:
    if _language() == "zh":
        diary_header = f"OSS PR Agent 每日日记已写入 {review.get('diary_path')}"
        lessons_header = f"OSS PR Agent 今日新增经验已写入 {review.get('agent_path')}"
    else:
        diary_header = f"OSS PR Agent daily diary written to {review.get('diary_path')}"
        lessons_header = f"OSS PR Agent lessons written to {review.get('agent_path')}"
    _send_long(cfg, diary_header, review.get("diary") or "")
    _send_long(cfg, lessons_header, review.get("lessons") or "")


def notify_experience_consolidated(cfg: dict[str, Any], summary: dict[str, str]) -> None:
    if _language() == "zh":
        header = f"OSS PR Agent 已完成阶段性经验压缩，写入 {summary.get('agent_path')}"
    else:
        header = f"OSS PR Agent consolidated long-term lessons into {summary.get('agent_path')}"
    _send_long(cfg, header, summary.get("summary") or "")


def notify_goodnight(cfg: dict[str, Any]) -> None:
    if _language() == "zh":
        message = "晚安。OSS PR Agent 已完成睡前复盘，本 agent 也要休息了。\n写代码虽好，可不要贪杯呦。"
    else:
        message = "Good night. OSS PR Agent has completed its pre-sleep review and is going to rest."
    send(cfg, message)


def notify_failed_human(cfg: dict[str, Any], task: dict[str, Any], reason: str) -> None:
    if _language() == "zh":
        lines = [
            "OSS PR Agent 需要人工检查。",
            f"仓库：{task.get('repo')}",
            f"Issue：{task.get('issue_url') or task.get('issue_number')}",
            f"PR：{task.get('pr_url') or '无'}",
            f"任务：{task.get('id')}",
            f"原因：{reason}",
            "详情：/root/.zedclaw/oss_pr_agent/human_review.md",
        ]
    else:
        lines = [
            "OSS PR Agent needs human review.",
            f"Repo: {task.get('repo')}",
            f"Issue: {task.get('issue_url') or task.get('issue_number')}",
            f"PR: {task.get('pr_url') or '(none)'}",
            f"Task: {task.get('id')}",
            f"Reason: {reason}",
            "Details: /root/.zedclaw/oss_pr_agent/human_review.md",
        ]
    send(
        cfg,
        "\n".join(lines),
    )
