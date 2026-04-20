# claude-timebox-mini

A personal project. Shared in case it's useful for somebody else, but not built for general use — aimed specifically at one integration: **Claude Code → Divoom Timebox Mini**, driving the Mini's LED matrix as a visual status indicator during Claude Code sessions.

## What it does

The plugin registers Claude Code hooks that fire HTTP GETs at a small daemon you run yourself. The daemon translates them into Bluetooth commands to the Mini.

| Claude Code state | Mini display |
|---|---|
| Thinking / working | Clawd silhouette pulsing orange |
| Waiting on your input | Clawd flashing red |
| Turn complete | Clawd solid green for 3 s, then teal clock |
| Idle | Teal clock |

## What you need

- A **Divoom Timebox Mini** (Bluetooth Classic — not the Timebox Evo, which uses a different protocol).
- A host with **Bluetooth** to run the daemon. Pair and trust the Mini on it first (`bluetoothctl pair <MAC>`, `bluetoothctl trust <MAC>`).
- **Claude Code** on the machine you code from.

## Install the plugin

In Claude Code:

```text
/plugin marketplace add git@github.com:danjam/claude-timebox-mini.git
/plugin install claude-timebox-mini
```

Then set the hook env vars somewhere Claude Code will pick them up (your shell profile, your user-level `~/.claude/settings.json` `env` block, etc.):

| Variable | Required | Description |
|---|---|---|
| `CLAUDE_TIMEBOX_MINI_BASE_URL` | yes | Base URL where your daemon is reachable, scheme included (e.g. `http://mini-host.local:25293` or `https://mini.example.com`). |
| `CLAUDE_TIMEBOX_MINI_API_KEY` | no | Bearer token sent as `Authorization: Bearer <token>`. Set this to the same value as the daemon's `CLAUDE_TIMEBOX_MINI_API_KEY`. Omit if your daemon has auth disabled. |
| `CLAUDE_TIMEBOX_MINI_ALLOWED_GATEWAYS` | no | Comma-separated default-gateway MACs. If set, hooks only fire when your laptop's default gateway matches one — useful if you only want the Mini lit up when you're on your home network. Leave empty to always fire. |

## Run the daemon

The daemon source is `src/daemon.py`. It's stdlib-only Python, opens a single long-lived RFCOMM socket to the Mini, and exposes these HTTP GETs:

- `/thinking`, `/waiting`, `/done`, `/reset` — state changes
- `/ping` — reachability probe; returns `200 OK` without touching the Mini

Env vars:

| Variable | Required | Description |
|---|---|---|
| `CLAUDE_TIMEBOX_MINI_MAC` | yes | The Mini's Bluetooth MAC. |
| `CLAUDE_TIMEBOX_MINI_API_KEY` | no | If set, the daemon requires `Authorization: Bearer <this>` on all endpoints except `/ping`. Leave unset to disable auth. |

Deployment is up to you — the plugin only needs the five HTTP endpoints to answer. A few options:

### Bare Python

```bash
CLAUDE_TIMEBOX_MINI_MAC=XX:XX:XX:XX:XX:XX python3 -u src/daemon.py
```

### Docker (example `compose.yaml`)

A `Dockerfile` is at the repo root. Example compose:

```yaml
services:
  claude-timebox-mini:
    build: .
    container_name: claude-timebox-mini
    network_mode: host
    restart: unless-stopped
    environment:
      TZ: Europe/London
      CLAUDE_TIMEBOX_MINI_MAC: ${CLAUDE_TIMEBOX_MINI_MAC}
      # CLAUDE_TIMEBOX_MINI_API_KEY: ${CLAUDE_TIMEBOX_MINI_API_KEY}  # optional
```

`network_mode: host` is needed so the container can open Bluetooth sockets. Set `CLAUDE_TIMEBOX_MINI_MAC` in a `.env` file next to the compose or in your shell.

Adapt however you like — reverse proxy it, run it behind Traefik, deploy to a different host, run it directly on macOS without Docker — the plugin doesn't care.

## License

[MIT](LICENSE).
