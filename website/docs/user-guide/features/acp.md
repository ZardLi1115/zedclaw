---
sidebar_position: 11
title: "ACP Editor Integration"
description: "Use ZedClaw inside ACP-compatible editors such as VS Code, Zed, and JetBrains"
---

# ACP Editor Integration

ZedClaw can run as an ACP server, letting ACP-compatible editors talk to ZedClaw over stdio and render:

- chat messages
- tool activity
- file diffs
- terminal commands
- approval prompts
- streamed thinking / response chunks

ACP is a good fit when you want ZedClaw to behave like an editor-native coding agent instead of a standalone CLI or messaging bot.

## What ZedClaw exposes in ACP mode

ZedClaw runs with a curated `zedclaw-acp` toolset designed for editor workflows. It includes:

- file tools: `read_file`, `write_file`, `patch`, `search_files`
- terminal tools: `terminal`, `process`
- web/browser tools
- memory, todo, session search
- skills
- execute_code and delegate_task
- vision

It intentionally excludes things that do not fit typical editor UX, such as messaging delivery and cronjob management.

## Installation

Install ZedClaw normally, then add the ACP extra:

```bash
pip install -e '.[acp]'
```

This installs the `agent-client-protocol` dependency and enables:

- `zedclaw acp`
- `zedclaw-acp`
- `python -m acp_adapter`

## Launching the ACP server

Any of the following starts ZedClaw in ACP mode:

```bash
zedclaw acp
```

```bash
zedclaw-acp
```

```bash
python -m acp_adapter
```

ZedClaw logs to stderr so stdout remains reserved for ACP JSON-RPC traffic.

## Editor setup

### VS Code

Install the [ACP Client](https://marketplace.visualstudio.com/items?itemName=formulahendry.acp-client) extension.

To connect:

1. Open the ACP Client panel from the Activity Bar.
2. Select **ZedClaw** from the built-in agent list.
3. Connect and start chatting.

If you want to define ZedClaw manually, add it through VS Code settings under `acp.agents`:

```json
{
  "acp.agents": {
    "ZedClaw": {
      "command": "zedclaw",
      "args": ["acp"]
    }
  }
}
```

### Zed

Example settings snippet:

```json
{
  "agent_servers": {
    "zedclaw": {
      "type": "custom",
      "command": "zedclaw",
      "args": ["acp"],
    },
  },
}
```

### JetBrains

Use an ACP-compatible plugin and point it at:

```text
/path/to/zedclaw/acp_registry
```

## Registry manifest

The ACP registry manifest lives at:

```text
acp_registry/agent.json
```

It advertises a command-based agent whose launch command is:

```text
zedclaw acp
```

## Configuration and credentials

ACP mode uses the same ZedClaw configuration as the CLI:

- `~/.zedclaw/.env`
- `~/.zedclaw/config.yaml`
- `~/.zedclaw/skills/`
- `~/.zedclaw/state.db`

Provider resolution uses ZedClaw' normal runtime resolver, so ACP inherits the currently configured provider and credentials.

## Session behavior

ACP sessions are tracked by the ACP adapter's in-memory session manager while the server is running.

Each session stores:

- session ID
- working directory
- selected model
- current conversation history
- cancel event

The underlying `AIAgent` still uses ZedClaw' normal persistence/logging paths, but ACP `list/load/resume/fork` are scoped to the currently running ACP server process.

## Working directory behavior

ACP sessions bind the editor's cwd to the ZedClaw task ID so file and terminal tools run relative to the editor workspace, not the server process cwd.

## Approvals

Dangerous terminal commands can be routed back to the editor as approval prompts. ACP approval options are simpler than the CLI flow:

- allow once
- allow always
- deny

On timeout or error, the approval bridge denies the request.

## Troubleshooting

### ACP agent does not appear in the editor

Check:

- the editor is pointed at the correct `acp_registry/` path
- ZedClaw is installed and on your PATH
- the ACP extra is installed (`pip install -e '.[acp]'`)

### ACP starts but immediately errors

Try these checks:

```bash
zedclaw doctor
zedclaw status
zedclaw acp
```

### Missing credentials

ACP mode does not have its own login flow. It uses ZedClaw' existing provider setup. Configure credentials with:

```bash
zedclaw model
```

or by editing `~/.zedclaw/.env`.

## See also

- [ACP Internals](../../developer-guide/acp-internals.md)
- [Provider Runtime Resolution](../../developer-guide/provider-runtime.md)
- [Tools Runtime](../../developer-guide/tools-runtime.md)
