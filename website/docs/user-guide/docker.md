---
sidebar_position: 7
title: "Docker"
description: "Running ZedClaw in Docker and using Docker as a terminal backend"
---

# ZedClaw — Docker

There are two distinct ways Docker intersects with ZedClaw:

1. **Running ZedClaw IN Docker** — the agent itself runs inside a container (this page's primary focus)
2. **Docker as a terminal backend** — the agent runs on your host but executes every command inside a single, persistent Docker sandbox container that survives across tool calls, `/new`, and subagents for the life of the ZedClaw process (see [Configuration → Docker Backend](./configuration.md#docker-backend))

This page covers option 1. The container stores all user data (config, API keys, sessions, skills, memories) in a single directory mounted from the host at `/opt/data`. The image itself is stateless and can be upgraded by pulling a new version without losing any configuration.

## Quick start

If this is your first time running ZedClaw, create a data directory on the host and start the container interactively to run the setup wizard:

```sh
mkdir -p ~/.zedclaw
docker run -it --rm \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw setup
```

This drops you into the setup wizard, which will prompt you for your API keys and write them to `~/.zedclaw/.env`. You only need to do this once. It is highly recommended to set up a chat system for the gateway to work with at this point.

## Running in gateway mode

Once configured, run the container in the background as a persistent gateway (Telegram, Discord, Slack, WhatsApp, etc.):

```sh
docker run -d \
  --name zedclaw \
  --restart unless-stopped \
  -v ~/.zedclaw:/opt/data \
  -p 8642:8642 \
  nousresearch/zedclaw gateway run
```

Port 8642 exposes the gateway's [OpenAI-compatible API server](./features/api-server.md) and health endpoint. It's optional if you only use chat platforms (Telegram, Discord, etc.), but required if you want the dashboard or external tools to reach the gateway.

Note: the API server is gated on `API_SERVER_ENABLED=true`. To expose it beyond `127.0.0.1` inside the container, also set `API_SERVER_HOST=0.0.0.0` and an `API_SERVER_KEY` (minimum 8 characters — generate one with `openssl rand -hex 32`). Example:

```sh
docker run -d \
  --name zedclaw \
  --restart unless-stopped \
  -v ~/.zedclaw:/opt/data \
  -p 8642:8642 \
  -e API_SERVER_ENABLED=true \
  -e API_SERVER_HOST=0.0.0.0 \
  -e API_SERVER_KEY=your_api_key_here \
  -e API_SERVER_CORS_ORIGINS='*' \
  nousresearch/zedclaw gateway run
```

Opening any port on an internet facing machine is a security risk. You should not do it unless you understand the risks.

## Running the dashboard

The built-in web dashboard runs as an optional side-process inside the same container as the gateway. Set `ZEDCLAW_DASHBOARD=1` and expose port `9119` alongside the gateway's `8642`:

```sh
docker run -d \
  --name zedclaw \
  --restart unless-stopped \
  -v ~/.zedclaw:/opt/data \
  -p 8642:8642 \
  -p 9119:9119 \
  -e ZEDCLAW_DASHBOARD=1 \
  nousresearch/zedclaw gateway run
```

The entrypoint starts `zedclaw dashboard` in the background (running as the non-root `zedclaw` user) before `exec`-ing the main command. Dashboard output is prefixed with `[dashboard]` in `docker logs` so it's easy to separate from gateway logs.

| Environment variable | Description | Default |
|---------------------|-------------|---------|
| `ZEDCLAW_DASHBOARD` | Set to `1` (or `true` / `yes`) to launch the dashboard alongside the main command | *(unset — dashboard not started)* |
| `ZEDCLAW_DASHBOARD_HOST` | Bind address for the dashboard HTTP server | `0.0.0.0` |
| `ZEDCLAW_DASHBOARD_PORT` | Port for the dashboard HTTP server | `9119` |
| `ZEDCLAW_DASHBOARD_TUI` | Set to `1` to expose the in-browser Chat tab (embedded `zedclaw --tui` via PTY/WebSocket) | *(unset)* |

The default `ZEDCLAW_DASHBOARD_HOST=0.0.0.0` is required for the host to reach the dashboard through the published port; the entrypoint automatically passes `--insecure` to `zedclaw dashboard` in that case. Override to `127.0.0.1` if you want to restrict the dashboard to in-container access only (e.g. behind a reverse proxy in a sidecar).

:::note
The dashboard side-process is **not supervised** — if it crashes, it stays down until the container restarts. Running it as a separate container is not supported: the dashboard's gateway-liveness detection requires a shared PID namespace with the gateway process.
:::

## Running interactively (CLI chat)

To open an interactive chat session against a running data directory:

```sh
docker run -it --rm \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw
```

Or if you have already opened a terminal in your running container (via Docker Desktop for instance), just run:

```sh
/opt/zedclaw/.venv/bin/zedclaw
```

## Persistent volumes

The `/opt/data` volume is the single source of truth for all ZedClaw state. It maps to your host's `~/.zedclaw/` directory and contains:

| Path | Contents |
|------|----------|
| `.env` | API keys and secrets |
| `config.yaml` | All ZedClaw configuration |
| `SOUL.md` | Agent personality/identity |
| `sessions/` | Conversation history |
| `memories/` | Persistent memory store |
| `skills/` | Installed skills |
| `cron/` | Scheduled job definitions |
| `hooks/` | Event hooks |
| `logs/` | Runtime logs |
| `skins/` | Custom CLI skins |

:::warning
Never run two ZedClaw **gateway** containers against the same data directory simultaneously — session files and memory stores are not designed for concurrent write access.
:::

## Multi-profile support

ZedClaw supports [multiple profiles](../reference/profile-commands.md) — separate `~/.zedclaw/` directories that let you run independent agents (different SOUL, skills, memory, sessions, credentials) from a single installation. **When running under Docker, using ZedClaw' built-in multi-profile feature is not recommended.**

Instead, the recommended pattern is **one container per profile**, with each container bind-mounting its own host directory as `/opt/data`:

```sh
# Work profile
docker run -d \
  --name zedclaw-work \
  --restart unless-stopped \
  -v ~/.zedclaw-work:/opt/data \
  -p 8642:8642 \
  nousresearch/zedclaw gateway run

# Personal profile
docker run -d \
  --name zedclaw-personal \
  --restart unless-stopped \
  -v ~/.zedclaw-personal:/opt/data \
  -p 8643:8642 \
  nousresearch/zedclaw gateway run
```

Why separate containers over profiles in Docker:

- **Isolation** — each container has its own filesystem, process table, and resource limits. A crash, dependency change, or runaway session in one profile can't affect another.
- **Independent lifecycle** — upgrade, restart, pause, or roll back each agent separately (`docker restart zedclaw-work` leaves `zedclaw-personal` untouched).
- **Clean port and network separation** — each gateway binds its own host port; there's no risk of cross-talk between chat platforms or API servers.
- **Simpler mental model** — the container *is* the profile. Backups, migrations, and permissions all follow the bind-mounted directory, with no extra `--profile` flags to remember.
- **Avoids concurrent-write risk** — the warning above about never running two gateways against the same data directory still applies to profiles within a single container.

In Docker Compose, this just means declaring one service per profile with distinct `container_name`, `volumes`, and `ports`:

```yaml
services:
  zedclaw-work:
    image: nousresearch/zedclaw:latest
    container_name: zedclaw-work
    restart: unless-stopped
    command: gateway run
    ports:
      - "8642:8642"
    volumes:
      - ~/.zedclaw-work:/opt/data

  zedclaw-personal:
    image: nousresearch/zedclaw:latest
    container_name: zedclaw-personal
    restart: unless-stopped
    command: gateway run
    ports:
      - "8643:8642"
    volumes:
      - ~/.zedclaw-personal:/opt/data
```

## Environment variable forwarding

API keys are read from `/opt/data/.env` inside the container. You can also pass environment variables directly:

```sh
docker run -it --rm \
  -v ~/.zedclaw:/opt/data \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e OPENAI_API_KEY="sk-..." \
  nousresearch/zedclaw
```

Direct `-e` flags override values from `.env`. This is useful for CI/CD or secrets-manager integrations where you don't want keys on disk.

## Docker Compose example

For persistent deployment with both the gateway and dashboard, a `docker-compose.yaml` is convenient:

```yaml
services:
  zedclaw:
    image: nousresearch/zedclaw:latest
    container_name: zedclaw
    restart: unless-stopped
    command: gateway run
    ports:
      - "8642:8642"   # gateway API
      - "9119:9119"   # dashboard (only reached when ZEDCLAW_DASHBOARD=1)
    volumes:
      - ~/.zedclaw:/opt/data
    environment:
      - ZEDCLAW_DASHBOARD=1
      # Uncomment to forward specific env vars instead of using .env file:
      # - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      # - OPENAI_API_KEY=${OPENAI_API_KEY}
      # - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "2.0"
```

Start with `docker compose up -d` and view logs with `docker compose logs -f`. Dashboard output is prefixed with `[dashboard]` so it's easy to filter from gateway logs.

## Resource limits

The ZedClaw container needs moderate resources. Recommended minimums:

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| Memory | 1 GB | 2–4 GB |
| CPU | 1 core | 2 cores |
| Disk (data volume) | 500 MB | 2+ GB (grows with sessions/skills) |

Browser automation (Playwright/Chromium) is the most memory-hungry feature. If you don't need browser tools, 1 GB is sufficient. With browser tools active, allocate at least 2 GB.

Set limits in Docker:

```sh
docker run -d \
  --name zedclaw \
  --restart unless-stopped \
  --memory=4g --cpus=2 \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw gateway run
```

## What the Dockerfile does

The official image is based on `debian:13.4` and includes:

- Python 3 with all ZedClaw dependencies (`uv pip install -e ".[all]"`)
- Node.js + npm (for browser automation and WhatsApp bridge)
- Playwright with Chromium (`npx playwright install --with-deps chromium --only-shell`)
- ripgrep, ffmpeg, git, and tini as system utilities
- **`docker-cli`** — so agents running inside the container can drive the host's Docker daemon (bind-mount `/var/run/docker.sock` to opt in) for `docker build`, `docker run`, container inspection, etc.
- **`openssh-client`** — enables the [SSH terminal backend](/docs/user-guide/configuration#ssh-backend) from inside the container. The SSH backend shells out to the system `ssh` binary; without this, it failed silently in containerized installs.
- The WhatsApp bridge (`scripts/whatsapp-bridge/`)

The entrypoint script (`docker/entrypoint.sh`) bootstraps the data volume on first run:
- Creates the directory structure (`sessions/`, `memories/`, `skills/`, etc.)
- Copies `.env.example` → `.env` if no `.env` exists
- Copies default `config.yaml` if missing
- Copies default `SOUL.md` if missing
- Syncs bundled skills using a manifest-based approach (preserves user edits)
- Optionally launches `zedclaw dashboard` as a background side-process when `ZEDCLAW_DASHBOARD=1` (see [Running the dashboard](#running-the-dashboard))
- Then runs `zedclaw` with whatever arguments you pass

:::warning
Do not override the image entrypoint unless you keep `/opt/zedclaw/docker/entrypoint.sh` in the command chain. The entrypoint drops root privileges to the `zedclaw` user before gateway state files are created. Starting `zedclaw gateway run` as root inside the official image is refused by default because it can leave root-owned files in `/opt/data` and break later dashboard or gateway starts. Set `ZEDCLAW_ALLOW_ROOT_GATEWAY=1` only when you intentionally accept that risk.
:::

## Upgrading

Pull the latest image and recreate the container. Your data directory is untouched.

```sh
docker pull nousresearch/zedclaw:latest
docker rm -f zedclaw
docker run -d \
  --name zedclaw \
  --restart unless-stopped \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw gateway run
```

Or with Docker Compose:

```sh
docker compose pull
docker compose up -d
```

## Skills and credential files

When using Docker as the execution environment (not the methods above, but when the agent runs commands inside a Docker sandbox — see [Configuration → Docker Backend](./configuration.md#docker-backend)), ZedClaw reuses a single long-lived container for all tool calls and automatically bind-mounts the skills directory (`~/.zedclaw/skills/`) and any credential files declared by skills into that container as read-only volumes. Skill scripts, templates, and references are available inside the sandbox without manual configuration, and because the container persists for the life of the ZedClaw process, any dependencies you install or files you write stay around for the next tool call.

The same syncing happens for SSH and Modal backends — skills and credential files are uploaded via rsync or the Modal mount API before each command.

## Connecting to local inference servers (vLLM, Ollama, etc.)

When running ZedClaw in Docker and your inference server (vLLM, Ollama, text-generation-inference, etc.) is also running on the host or in another container, networking requires extra attention.

### Docker Compose (recommended)

Put both services on the same Docker network. This is the most reliable approach:

```yaml
services:
  vllm:
    image: vllm/vllm-openai:latest
    container_name: vllm
    command: >
      --model Qwen/Qwen2.5-7B-Instruct
      --served-model-name my-model
      --host 0.0.0.0
      --port 8000
    ports:
      - "8000:8000"
    networks:
      - zedclaw-net
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  zedclaw:
    image: nousresearch/zedclaw:latest
    container_name: zedclaw
    restart: unless-stopped
    command: gateway run
    ports:
      - "8642:8642"
    volumes:
      - ~/.zedclaw:/opt/data
    networks:
      - zedclaw-net

networks:
  zedclaw-net:
    driver: bridge
```

Then in your `~/.zedclaw/config.yaml`, use the **container name** as the hostname:

```yaml
model:
  provider: custom
  model: my-model
  base_url: http://vllm:8000/v1
  api_key: "none"
```

:::tip Key points
- Use the **container name** (`vllm`) as the hostname — not `localhost` or `127.0.0.1`, which refer to the ZedClaw container itself.
- The `model` value must match the `--served-model-name` you passed to vLLM.
- Set `api_key` to any non-empty string (vLLM requires the header but doesn't validate it by default).
- Do **not** include a trailing slash in `base_url`.
:::

### Standalone Docker run (no Compose)

If your inference server runs directly on the host (not in Docker), use `host.docker.internal` on macOS/Windows, or `--network host` on Linux:

**macOS / Windows:**

```sh
docker run -d \
  --name zedclaw \
  -v ~/.zedclaw:/opt/data \
  -p 8642:8642 \
  nousresearch/zedclaw gateway run
```

```yaml
# config.yaml
model:
  provider: custom
  model: my-model
  base_url: http://host.docker.internal:8000/v1
  api_key: "none"
```

**Linux (host networking):**

```sh
docker run -d \
  --name zedclaw \
  --network host \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw gateway run
```

```yaml
# config.yaml
model:
  provider: custom
  model: my-model
  base_url: http://127.0.0.1:8000/v1
  api_key: "none"
```

:::warning With `--network host`, the `-p` flag is ignored — all container ports are directly exposed on the host.
:::

### Verifying connectivity

From inside the ZedClaw container, confirm the inference server is reachable:

```sh
docker exec zedclaw curl -s http://vllm:8000/v1/models
```

You should see a JSON response listing your served model. If this fails, check:

1. Both containers are on the same Docker network (`docker network inspect zedclaw-net`)
2. The inference server is listening on `0.0.0.0`, not `127.0.0.1`
3. The port number matches

### Ollama

Ollama works the same way. If Ollama runs on the host, use `host.docker.internal:11434` (macOS/Windows) or `127.0.0.1:11434` (Linux with `--network host`). If Ollama runs in its own container on the same Docker network:

```yaml
model:
  provider: custom
  model: llama3
  base_url: http://ollama:11434/v1
  api_key: "none"
```

## Troubleshooting

### Container exits immediately

Check logs: `docker logs zedclaw`. Common causes:
- Missing or invalid `.env` file — run interactively first to complete setup
- Port conflicts if running with exposed ports

### "Permission denied" errors

The container's entrypoint drops privileges to the non-root `zedclaw` user (UID 10000) via `gosu`. If your host `~/.zedclaw/` is owned by a different UID, set `ZEDCLAW_UID`/`ZEDCLAW_GID` to match your host user, or ensure the data directory is writable:

```sh
chmod -R 755 ~/.zedclaw
```

### Browser tools not working

Playwright needs shared memory. Add `--shm-size=1g` to your Docker run command:

```sh
docker run -d \
  --name zedclaw \
  --shm-size=1g \
  -v ~/.zedclaw:/opt/data \
  nousresearch/zedclaw gateway run
```

### Gateway not reconnecting after network issues

The `--restart unless-stopped` flag handles most transient failures. If the gateway is stuck, restart the container:

```sh
docker restart zedclaw
```

### Checking container health

```sh
docker logs --tail 50 zedclaw          # Recent logs
docker run -it --rm nousresearch/zedclaw:latest version     # Verify version
docker stats zedclaw                    # Resource usage
```
