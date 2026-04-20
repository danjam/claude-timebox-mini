#!/usr/bin/env bash
# claude-timebox-mini-hook.sh <thinking|waiting|done|reset>
#
# Fires a GET at CLAUDE_TIMEBOX_MINI_BASE_URL (full URL including scheme).
# Optionally sends a bearer token via CLAUDE_TIMEBOX_MINI_API_KEY.
# Optionally gates on the default-gateway MAC so the hook only fires when
# Claude Code is running on a trusted network.
set -eu
state="${1:-}"
base_url="${CLAUDE_TIMEBOX_MINI_BASE_URL:-}"
api_key="${CLAUDE_TIMEBOX_MINI_API_KEY:-}"
allowed="${CLAUDE_TIMEBOX_MINI_ALLOWED_GATEWAYS:-}"

[ -n "$state" ] || exit 0
[ -n "$base_url" ] || exit 0

# Notification filter: Claude Code's Notification hook fires for multiple
# reasons (idle_prompt, auth_success, …). Allowlist only the two types that
# genuinely mean "Claude is blocked on user input". PreToolUse and
# PermissionRequest events don't carry notification_type and pass through
# unfiltered — they're trusted to only fire when the user is actually needed.
if [ "$state" = "waiting" ]; then
    payload=$(cat)
    if echo "$payload" | grep -q '"notification_type"'; then
        if ! echo "$payload" | grep -qE '"notification_type": *"(permission_prompt|elicitation_dialog)"'; then
            exit 0
        fi
    fi
fi

if [ -n "$allowed" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        gw=$(route -n get default 2>/dev/null | awk '/gateway:/ {print $2}')
        mac=$(arp -n "$gw" 2>/dev/null | awk '{print $4}')
    else
        gw=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')
        mac=$(ip neigh 2>/dev/null | awk -v g="$gw" '$1==g {print $5; exit}')
    fi
    echo "$allowed" | tr ',' '\n' | grep -qi "^${mac}$" || exit 0
fi

auth=()
[ -n "$api_key" ] && auth=(-H "Authorization: Bearer $api_key")
curl -fsS --max-time 2 "${auth[@]}" "${base_url%/}/${state}" >/dev/null 2>&1 || true
