from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from zedclaw_cli.config import load_config

from . import github_ops, llm, notifier, state


def normalize_repo(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s#?]+)", text)
    if match:
        owner = match.group(1)
        repo = match.group(2).removesuffix(".git")
        return f"{owner}/{repo}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        return text
    raise ValueError("Invalid GitHub repository. Use `/prefer https://github.com/owner/repo`.")


def _cfg() -> dict[str, Any]:
    cfg = load_config()
    return cfg if isinstance(cfg, dict) else {}


def _agent_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}


def _gh_json(args: list[str], *, default: Any) -> Any:
    proc = github_ops.run(args, timeout=120)
    if proc.returncode != 0:
        return default
    try:
        return json.loads(proc.stdout or "")
    except Exception:
        return default


def _repo_context(repo: str) -> dict[str, Any]:
    info = _gh_json(
        [
            "gh",
            "repo",
            "view",
            repo,
            "--json",
            "nameWithOwner,description,stargazerCount,pushedAt,updatedAt,isArchived,defaultBranchRef,primaryLanguage",
        ],
        default={},
    )
    issues = _gh_json(
        [
            "gh",
            "issue",
            "list",
            "-R",
            repo,
            "--state",
            "open",
            "--limit",
            "20",
            "--json",
            "number,title,url,labels,updatedAt,body",
        ],
        default=[],
    )
    root = _gh_json(["gh", "api", f"repos/{repo}/contents"], default=[])
    tree = [
        {
            "name": item.get("name"),
            "type": item.get("type"),
            "path": item.get("path"),
        }
        for item in root[:80]
        if isinstance(item, dict)
    ]
    return {"repo": repo, "repo_info": info, "open_issues": issues, "root_tree": tree}


def _format_plan(plan_id: str, repo: str, plan: dict[str, Any], *, lang: str) -> str:
    title = str(plan.get("title") or "Untitled preferred PR plan")
    issue = plan.get("issue_url") or plan.get("issue_number") or "none"
    summary = str(plan.get("summary") or "")
    steps = plan.get("implementation_plan") or []
    if isinstance(steps, str):
        steps_text = steps
    else:
        steps_text = "\n".join(f"- {step}" for step in steps[:8])
    risk = str(plan.get("risk") or "")
    if lang == "zh":
        return "\n".join(
            [
                "OSS PR Agent 发现一个重点关注 PR 机会。",
                f"计划 ID：{plan_id}",
                f"仓库：{repo}",
                f"标题：{title}",
                f"Issue：{issue}",
                f"摘要：{summary}",
                "计划：",
                steps_text or "- 无",
                f"风险：{risk or '未说明'}",
                "",
                f"同意后发送：`/prefer approve {plan_id}`",
                f"拒绝则发送：`/prefer reject {plan_id}`",
            ]
        )
    return "\n".join(
        [
            "OSS PR Agent found a preferred PR opportunity.",
            f"Plan ID: {plan_id}",
            f"Repo: {repo}",
            f"Title: {title}",
            f"Issue: {issue}",
            f"Summary: {summary}",
            "Plan:",
            steps_text or "- none",
            f"Risk: {risk or 'not specified'}",
            "",
            f"Approve with: `/prefer approve {plan_id}`",
            f"Reject with: `/prefer reject {plan_id}`",
        ]
    )


def _language() -> str:
    try:
        from oss_pr_agent.status import get_language

        return get_language()
    except Exception:
        return "en"


def analyze_once(repo_url: str) -> str:
    repo = normalize_repo(repo_url)
    cfg = _cfg()
    lang = _language()
    if not github_ops.gh_available() or not github_ops.gh_auth_ok():
        raise RuntimeError("GitHub CLI is unavailable or unauthenticated.")
    with state.connect() as conn:
        cooldown = state.repo_cooldown(conn, repo)
    if cooldown:
        until = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(cooldown.get("until_at") or 0)))
        if lang == "zh":
            return f"重点关注分析未启动：{repo} 处于冷静期，结束时间 {until}。"
        return f"Preferred analysis skipped: {repo} is in cooldown until {until}."

    eligible, reason, stars = github_ops._repo_eligible(
        repo,
        max_inactive_days=int(_agent_cfg(cfg).get("max_repo_inactive_days", 30) or 30),
        min_stars=int(_agent_cfg(cfg).get("min_repo_stars", 100) or 100),
    )
    if not eligible:
        if lang == "zh":
            return f"重点关注分析未启动：{repo} 不满足仓库筛选条件（{reason}）。"
        return f"Preferred analysis skipped: {repo} is not eligible ({reason})."

    context = _repo_context(repo)
    with state.connect() as conn:
        merged_count = state.merged_pr_count(conn, repo)
    context["repo_merged_pr_count"] = merged_count
    context["small_pr_preferred"] = merged_count < int(_agent_cfg(cfg).get("small_pr_until_merged_count", 3) or 3)
    model = str(_agent_cfg(cfg).get("planner_model") or cfg.get("model", {}).get("default") or "gpt-5.5")
    plan = llm.call_json(
        model=model,
        system=(
            "You analyze one user-preferred GitHub repository for an autonomous OSS PR agent. "
            "Return strict JSON with keys: has_opportunity, title, issue_number, issue_url, "
            "summary, implementation_plan, risk, score. Prefer small valuable PRs that can be "
            "verified. If small_pr_preferred is true, avoid broad or risky PR ideas. Use repository "
            "issues and visible code structure. Do not invent issue URLs."
        ),
        user=json.dumps(context, ensure_ascii=False)[:60000],
        max_tokens=1200,
        temperature=0.2,
    )
    if not plan or not plan.get("has_opportunity"):
        message = f"重点关注分析完成：{repo} 暂未发现适合自动提交 PR 的机会。" if lang == "zh" else f"Preferred analysis completed: no suitable PR opportunity found in {repo}."
        notifier.send(cfg, message)
        return message

    plan_id = f"pref_{uuid.uuid4().hex[:8]}"
    issue_number = plan.get("issue_number")
    try:
        issue_number = int(issue_number) if issue_number else None
    except Exception:
        issue_number = None
    score = float(plan.get("score") or 0.95)
    stored = {
        "id": plan_id,
        "repo": repo,
        "repo_url": f"https://github.com/{repo}",
        "status": "pending_approval",
        "title": plan.get("title") or f"Preferred task for {repo}",
        "issue_url": plan.get("issue_url"),
        "issue_number": issue_number,
        "score": score,
        "plan_json": plan,
    }
    with state.connect() as conn:
        state.upsert_preferred_plan(conn, stored)
    message = _format_plan(plan_id, repo, plan, lang=lang)
    notifier.send(cfg, message)
    return message


def approve(plan_id: str) -> str:
    raw = str(plan_id or "").strip()
    if not raw:
        raise ValueError("Missing preferred plan id.")
    with state.connect() as conn:
        rows = state.list_preferred_plans(conn, ["pending_approval"])
        plan = next((item for item in rows if item["id"] == raw), None)
        if not plan:
            raise ValueError(f"Preferred plan not found or already handled: {raw}")
        state.update_preferred_plan(conn, raw, status="approved")
        state.set_meta(conn, "next_wake_at", time.time())
        state.set_meta(conn, "next_wake_reason", "preferred plan approved")
    if _language() == "zh":
        return f"已批准重点关注计划：{raw}。Runtime 下次唤醒会优先处理它。"
    return f"Approved preferred plan: {raw}. The runtime will prioritize it on the next wake."


def reject(plan_id: str) -> str:
    raw = str(plan_id or "").strip()
    if not raw:
        raise ValueError("Missing preferred plan id.")
    with state.connect() as conn:
        state.update_preferred_plan(conn, raw, status="rejected")
    return f"已拒绝重点关注计划：{raw}。" if _language() == "zh" else f"Rejected preferred plan: {raw}."


def list_pending() -> str:
    lang = _language()
    with state.connect() as conn:
        rows = state.list_preferred_plans(conn, ["pending_approval", "approved"])
    if not rows:
        return "当前没有待处理重点关注计划。" if lang == "zh" else "No preferred plans are pending."
    lines = ["重点关注计划：" if lang == "zh" else "Preferred plans:"]
    for item in rows:
        lines.append(f"- {item['id']} [{item['status']}] {item['repo']} - {item.get('title') or 'Untitled'}")
    return "\n".join(lines)


def next_approved_candidate(conn) -> dict[str, Any] | None:
    rows = state.list_preferred_plans(conn, ["approved"])
    if not rows:
        return None
    item = next((row for row in rows if not state.repo_in_cooldown(conn, row["repo"])), None)
    if not item:
        return None
    return {
        "repo": item["repo"],
        "issue_url": item.get("issue_url"),
        "issue_number": item.get("issue_number"),
        "title": item.get("title") or f"Preferred task for {item['repo']}",
        "score": max(float(item.get("score") or 0), 0.99),
        "preferred_plan_id": item["id"],
        "preferred_plan": item.get("plan_json") or {},
    }
