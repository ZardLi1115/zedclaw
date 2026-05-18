from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from . import state


def _run_dir(task_id: str) -> Path:
    path = state.agent_home() / "runs" / f"{int(time.time())}_{task_id}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _with_agent_experience(prompt: str) -> str:
    path = state.agent_home() / "agent.md"
    if not path.exists():
        return prompt
    memory = path.read_text(encoding="utf-8", errors="replace").strip()
    if not memory:
        return prompt
    return (
        "# OSS PR Agent Long-Term Experience\n\n"
        f"{memory}\n\n"
        "# Current Task Instructions\n\n"
        f"{prompt}"
    )


def run_codex(*, task: dict[str, Any], cfg: dict[str, Any], prompt: str, kind: str) -> dict[str, Any]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    codex_bin = str(agent_cfg.get("codex_path") or "codex")
    model = str(agent_cfg.get("codex_model") or "gpt-5.5")
    effort = str(agent_cfg.get("codex_reasoning_effort") or "low")
    timeout = int(agent_cfg.get("codex_timeout_seconds", 3600) or 3600)
    workspace = Path(task["workspace"])
    run_dir = _run_dir(task["id"])
    prompt_path = run_dir / "prompt.md"
    result_path = run_dir / "result.md"
    log_path = run_dir / "codex.log"
    prompt = _with_agent_experience(prompt)
    prompt_path.write_text(prompt, encoding="utf-8")
    cmd = [
        codex_bin,
        "exec",
        "-C", str(workspace),
        "--dangerously-bypass-approvals-and-sandbox",
        "-m", model,
        "-c", f'model_reasoning_effort="{effort}"',
        "-o", str(result_path),
        "-",
    ]
    started = time.time()
    env = os.environ.copy()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    base_url = str(model_cfg.get("base_url") or "").strip()
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    if base_url:
        env["OPENAI_BASE_URL"] = base_url

    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
    )
    log_path.write_text(proc.stdout or "", encoding="utf-8")
    return {
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "run_dir": str(run_dir),
        "log_path": str(log_path),
        "result_path": str(result_path),
        "started_at": started,
        "finished_at": time.time(),
        "output": (proc.stdout or "")[-4000:],
        "result": result_path.read_text(encoding="utf-8", errors="replace") if result_path.exists() else "",
    }


def initial_prompt(task: dict[str, Any]) -> str:
    return f"""You are an autonomous open-source contribution worker.

Goal: solve exactly this GitHub issue and open a PR.

Repository: {task['repo']}
Issue: {task.get('issue_url') or task.get('issue_number')}
Title: {task.get('title')}

Rules:
- Read the issue and repository contribution guidance first.
- Keep the change surgical and focused on the issue.
- Prefer adding or updating tests when practical.
- Run the relevant tests or checks you can run locally.
- Create a branch, commit the change, push it, and open a GitHub PR with gh.
- Use a clear PR body with summary, tests run, and issue link.
- If you cannot make a verifiable fix, stop and explain exactly why.
"""


def fix_prompt(task: dict[str, Any], pr_status: dict[str, Any], email_context: str = "") -> str:
    return f"""You are fixing an existing automated OSS PR.

Repository: {task['repo']}
Issue: {task.get('issue_url')}
PR: {task.get('pr_url')}
Fix attempt: {int(task.get('fix_attempts') or 0) + 1} of {int(task.get('max_fix_attempts') or 5)}

Current PR/check status:
{pr_status}

Relevant maintainer/email context:
{email_context or '(none)'}

Rules:
- Only fix the current PR failure or maintainer request.
- Do not expand scope or rewrite unrelated code.
- Inspect CI/check failures, run targeted tests locally, commit, and push to the same PR branch.
- If the failure cannot be fixed in this repository context, explain why and stop.
"""
