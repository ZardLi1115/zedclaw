<p align="center">
  <img src="assets/banner.png" alt="ZedClaw" width="100%">
</p>

# ZedClaw

**This project is based on [Hermes Agent](https://github.com/NousResearch/hermes-agent) and has been modified / further developed from it.**

## Credits

- Original author: [@NousResearch](https://github.com/NousResearch)
- Original repository: https://github.com/NousResearch/hermes-agent
- Main changes in this project:
  - Renamed and reoriented the project as ZedClaw.
  - Added an autonomous OSS PR Agent workflow for discovering GitHub issues and submitting pull requests.
  - Integrated Codex CLI based PR implementation and bounded fix-attempt loops.
  - Added GitHub/Gmail feedback intake, Feishu notifications, runtime status commands, language switching, and daily review behavior.

<p align="center">
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Language-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
</p>

ZedClaw is an autonomous coding-agent runtime focused on turning valuable open-source issues into pull requests. It can discover GitHub issues, choose suitable tasks, invoke Codex CLI to implement changes, monitor PR feedback, retry fixes, and notify you through messaging channels.

The project keeps the interactive agent experience, terminal tools, messaging gateway, slash commands, model switching, and scheduled runtime capabilities, then adds an OSS PR Agent workflow for agent, LLM, and harness engineering work.

The name comes from Zed, the Master of Shadows in *League of Legends*: ZedClaw is meant to act like a digital shadow clone for the user, spending tokens to complete pull-request work while the user stays focused on higher-level decisions.

Issues and pull requests are welcome!

This project has been published in the [LINUX DO community](https://linux.do). Thanks to the community for its support and feedback.

## Highlights

| Capability | What it does |
| --- | --- |
| Autonomous OSS PR Agent | Finds valuable GitHub issues and creates pull requests with Codex CLI. |
| Runtime scheduling | Lets the agent decide when to wake based on PR state, active work, and budget signals. |
| PR feedback loop | Watches GitHub and optional Gmail notifications, then schedules fix attempts when reviews or CI failures appear. |
| Bounded retries | Retries failed PR fixes up to a configured limit, then records true human-review items. |
| Feishu notifications | Sends updates when PRs are opened, fixes are pushed, tasks fail, or human review is needed. |
| Daily review | Summarizes daily PR outcomes and lessons into markdown notes and persistent agent memory. |
| Messaging commands | Provides low-cost runtime status through slash commands without asking the LLM. |
| Model flexibility | Supports OpenAI-compatible providers, OpenRouter, Codex OAuth, custom endpoints, and local/runtime tool execution. |

## OSS PR Agent

The OSS PR Agent is designed for unattended contribution work:

1. Discover repositories and issues in configured directions such as agent engineering, LLM tooling, eval harnesses, and developer automation.
2. Filter targets by repository quality, activity window, existing PR state, labels, issue content, and current workload.
3. Ask the planning model to choose the next wake time and task priority.
4. Invoke Codex CLI to inspect the repository, implement the change, test it, and submit a PR.
5. Monitor GitHub PR state, CI, reviews, comments, and optional Gmail notifications.
6. Re-run Codex CLI for fix attempts when feedback requires code changes.
7. Notify the operator through Feishu and expose runtime state through slash commands.

The default philosophy is pragmatic: keep moving while budget is available, avoid abandoned repositories, and reserve human review for cases the runtime cannot safely resolve.

## Quick Start

Clone the repository and install it in editable mode:

```bash
git clone <your-zedclaw-repo-url>
cd zedclaw
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

On Windows, you can use the PowerShell installer at `scripts/install.ps1`.

Start the CLI:

```bash
zedclaw
```

Run setup:

```bash
zedclaw setup
```

Configure only the OSS PR Agent:

```bash
zedclaw setup osspr
```

## Requirements

- Python 3.11 or newer
- Git and GitHub CLI (`gh`)
- Codex CLI available on `PATH` if OSS PR automation is enabled
- A model provider or OAuth-backed Codex provider configured through `zedclaw model`
- Optional: Feishu app credentials for notifications
- Optional: Gmail IMAP/app-password configuration for email-based PR feedback intake

## Common Commands

```bash
zedclaw                 # Start the interactive CLI
zedclaw setup           # Run the full setup wizard
zedclaw setup osspr     # Configure the OSS PR Agent
zedclaw model           # Choose model provider and model
zedclaw gateway         # Start the messaging gateway
zedclaw doctor          # Diagnose local configuration
```

Messaging and CLI slash commands:

| Command | Purpose |
| --- | --- |
| `/osspr` | Show OSS PR Agent runtime status, active task, PR count, merged PR count, and next wake time. |
| `/humanreview` | Show real human-review items that require operator action. |
| `/language` | Switch OSS PR Agent user-facing output between English and Chinese. |
| `/method` | Change the issue search theme, for example `/method all` or `/method eval harness`. |
| `/status` | Show messaging platform status where supported. |
| `/new` | Start a new conversation. |
| `/model` | Change the active model. |

## Configuration

Important OSS PR Agent settings include:

| Setting | Meaning |
| --- | --- |
| `oss_pr_agent.language` | Output language: `en` or `zh`. |
| `oss_pr_agent.focus_terms` | Search directions, or `all` for default agent/LLM/harness engineering directions. |
| `oss_pr_agent.codex_model` | Model used by Codex CLI for PR implementation. |
| `oss_pr_agent.codex_reasoning_effort` | Codex reasoning effort, for example `medium`. |
| `oss_pr_agent.max_fix_attempts` | Maximum automatic fix attempts before human review. |
| `oss_pr_agent.notify_target` | Notification target, commonly `feishu`. |
| `oss_pr_agent.min_repo_stars` | Minimum repository stars for candidate repositories. |
| `oss_pr_agent.repo_activity_window_days` | Maximum allowed inactivity window for candidate repositories. |
| `oss_pr_agent.budget_url` | Optional OpenAI/Anthropic-compatible usage endpoint; `usage`, `limits`, and `remaining` fields are treated as budget signals. |

Use the setup wizard when possible:

```bash
zedclaw setup osspr
```

## GitHub, Gmail, and Feishu

GitHub CLI is used for repository inspection, PR submission, PR checks, and review/comment polling:

```bash
gh auth login
gh auth status
```

Gmail integration is optional. When enabled, ZedClaw reads recent PR-related messages, deduplicates them against GitHub events, and classifies intent with a small model before scheduling work.

Feishu integration is optional but recommended for unattended operation. It is used to notify you when PRs are submitted, fix attempts are pushed, tasks fail, daily reviews complete, or human review is required.

## Development

Install development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

Run tests:

```bash
python -m pytest
```

Run targeted checks before submitting changes:

```bash
zedclaw doctor
python -m pytest tests/ -q
```

## Project Status

ZedClaw is actively evolving. The general agent runtime is usable, while the OSS PR Agent is intended for operators who are comfortable with unattended GitHub automation and can review PR behavior, API usage, and repository permissions.

Use a dedicated GitHub account or carefully scoped credentials for autonomous PR work.

## Roadmap

The current focus is autonomous pull-request work for open-source repositories. The long-term direction is broader: ZedClaw should grow from an OSS PR agent into a general task-execution runtime that can plan, schedule, execute, review, and report on many kinds of long-running digital work.

## License

MIT. See [LICENSE](LICENSE).
