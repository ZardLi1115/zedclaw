<p align="center">
  <img src="assets/banner.png" alt="ZedClaw" width="100%">
</p>

# ZedClaw

**本项目基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 修改/二次开发而来。**

## Credits

- 原作者：[@NousResearch](https://github.com/NousResearch)
- 原仓库：https://github.com/NousResearch/hermes-agent
- 本项目主要修改内容：
  - 将项目重命名并重新定位为 ZedClaw。
  - 新增自动化 OSS PR Agent 工作流，用于发现 GitHub issue 并提交 PR。
  - 集成基于 Codex CLI 的 PR 编写流程和有上限的自动重改循环。
  - 新增 GitHub/Gmail 反馈摄入、飞书通知、Runtime 状态指令、语言切换和每日复盘能力。

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/Language-English-lightgrey?style=for-the-badge" alt="English"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
</p>

ZedClaw 是一个面向自动化开源贡献的 Coding Agent Runtime。它可以自动寻找有价值的 GitHub issue，选择合适任务，调用 Codex CLI 修改代码并提交 PR，持续跟踪 PR 反馈，在需要时自动重改，并通过消息平台通知你。

项目保留了交互式 Agent、终端工具、消息网关、斜杠指令、模型切换和 Runtime 调度能力，并在此基础上加入了面向 Agent、LLM、Harness Engineering 方向的 OSS PR Agent 工作流。

ZedClaw 的命名来自游戏《英雄联盟》中的“影流之主 劫”：它希望成为用户的数字分身，替用户消耗 token，执行提交 PR 等需要持续投入的任务。

欢迎提交 Issue 和 Pull Request！

本项目已在 [LINUX DO 社区](https://linux.do) 发布，感谢社区的支持与反馈。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 自动化 OSS PR Agent | 自动发现 GitHub issue，并调用 Codex CLI 编写和提交 PR。 |
| Runtime 自主调度 | 根据 PR 状态、当前任务、预算信号和工作队列决定下一次唤醒时间。 |
| PR 反馈闭环 | 综合 GitHub 与可选 Gmail 通知，识别 review、CI、comment 和合并状态。 |
| 有上限的自动重改 | PR 校验或 review 未通过时自动修复，超过配置轮数后进入真实人工待办。 |
| 飞书通知 | PR 提交、重改、失败、进入人工待办、每日复盘等变化都会通知。 |
| 每日复盘 | 汇总当天 PR 进展、失败原因和经验教训，写入日记与 Agent 记忆。 |
| 消息端指令 | 通过斜杠指令直接查看 Runtime 状态，不需要额外调用大模型。 |
| 灵活模型配置 | 支持 OpenAI 兼容接口、OpenRouter、Codex OAuth、自定义端点和多种工具执行环境。 |

## OSS PR Agent

OSS PR Agent 的目标是无人值守地进行开源贡献：

1. 按配置方向搜索仓库和 issue，例如 Agent 工程、LLM 工具、Eval Harness、开发者自动化等。
2. 按仓库质量、活跃时间、stars、已有 PR、标签、issue 内容和当前工作负载筛选候选任务。
3. 让规划模型结合预算和 PR 进度决定下一次唤醒时间与任务优先级。
4. 调用 Codex CLI 检查仓库、实现代码、运行测试并提交 PR。
5. 监控 GitHub PR 状态、CI、review、comment，以及可选 Gmail 通知。
6. 遇到需要修改的反馈时，再次调用 Codex CLI 进行最多若干轮修复。
7. 通过飞书通知操作者，并通过斜杠指令暴露 Runtime 状态。

默认策略偏务实：预算充足时尽量推进更多 PR，避免长期无人维护的仓库，只把 Runtime 无法可靠处理的事项留给人工检查。

## 快速开始

克隆仓库并以 editable 模式安装：

```bash
git clone <your-zedclaw-repo-url>
cd zedclaw
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

Windows 用户可以使用 PowerShell 安装脚本：`scripts/install.ps1`。

启动 CLI：

```bash
zedclaw
```

运行完整配置：

```bash
zedclaw setup
```

只配置 OSS PR Agent：

```bash
zedclaw setup osspr
```

## 环境要求

- Python 3.11 或更新版本
- Git 和 GitHub CLI (`gh`)
- 如果启用 OSS PR 自动化，需要 Codex CLI 在 `PATH` 中可用
- 通过 `zedclaw model` 配置模型提供商，或配置 Codex OAuth provider
- 可选：飞书应用凭据，用于通知
- 可选：Gmail IMAP/app-password 配置，用于读取 PR 相关邮件反馈

## 常用命令

```bash
zedclaw                 # 启动交互式 CLI
zedclaw setup           # 运行完整配置向导
zedclaw setup osspr     # 配置 OSS PR Agent
zedclaw model           # 选择模型提供商和模型
zedclaw gateway         # 启动消息网关
zedclaw doctor          # 检查本地配置问题
```

CLI 与消息端常用斜杠指令：

| 指令 | 作用 |
| --- | --- |
| `/osspr` | 查看 OSS PR Agent Runtime 状态、当前任务、已提交 PR 数、已记录合并 PR 数和下次唤醒时间。 |
| `/humanreview` | 查看真实需要人工处理的待办事项。 |
| `/language` | 在中文和英文之间切换 OSS PR Agent 的用户可见输出。 |
| `/method` | 更换寻找 issue 的主题，例如 `/method all` 或 `/method eval harness`。 |
| `/status` | 查看消息平台状态，具体取决于平台支持。 |
| `/new` | 开启新会话。 |
| `/model` | 切换当前模型。 |

## 配置

OSS PR Agent 相关重要配置包括：

| 配置项 | 含义 |
| --- | --- |
| `oss_pr_agent.language` | 输出语言：`en` 或 `zh`。 |
| `oss_pr_agent.focus_terms` | 想提 PR 的方向；也可以设置为 `all` 使用默认 Agent/LLM/Harness Engineering 方向。 |
| `oss_pr_agent.codex_model` | Codex CLI 编写 PR 时使用的模型。 |
| `oss_pr_agent.codex_reasoning_effort` | Codex 推理强度，例如 `medium`。 |
| `oss_pr_agent.max_fix_attempts` | 自动修复失败 PR 的最大轮数。 |
| `oss_pr_agent.notify_target` | 通知目标，常用 `feishu`。 |
| `oss_pr_agent.min_repo_stars` | 候选仓库最低 star 数。 |
| `oss_pr_agent.repo_activity_window_days` | 候选仓库允许的最长未活跃时间。 |
| `oss_pr_agent.budget_url` | 可选的 OpenAI/Anthropic 兼容用量端点；返回的 `usage`、`limits`、`remaining` 字段会作为预算信号。 |

优先使用配置向导：

```bash
zedclaw setup osspr
```

## GitHub、Gmail 与飞书

GitHub CLI 用于仓库检查、PR 提交、PR checks、review/comment 查询：

```bash
gh auth login
gh auth status
```

Gmail 集成是可选项。启用后，ZedClaw 会读取最近的 PR 相关邮件，与 GitHub 事件去重，并用小模型判断邮件意图，再交给 Runtime 排期处理。

飞书集成也是可选项，但推荐在无人值守运行时启用。ZedClaw 会在提交 PR、推送重改、任务失败、进入人工待办、每日复盘完成时发送通知。

## 开发

安装开发依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,dev]"
```

运行测试：

```bash
python -m pytest
```

提交改动前建议运行：

```bash
zedclaw doctor
python -m pytest tests/ -q
```

## 项目状态

ZedClaw 仍在持续演进。通用 Agent Runtime 已可使用；
OSS PR Agent 面向能接受无人值守 GitHub 自动化的用户，需要操作者自行关注 PR 行为、API 用量和仓库权限。

建议为自动提 PR 使用专门的 GitHub 账号，或使用权限范围清晰的凭据。

## 未来方向

当前重点是面向开源仓库的自动提 PR。更长期的方向是把 ZedClaw 从 OSS PR Agent 扩展为更通用的任务执行 Runtime，让它可以规划、调度、执行、复盘并汇报更广泛的长期数字任务。

## 许可证

MIT。详见 [LICENSE](LICENSE)。
