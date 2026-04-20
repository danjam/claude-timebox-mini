#!/usr/bin/env bash
# claude-timebox-mini-hook.sh <thinking|waiting|done|reset>
#
# Fires a GET at CLAUDE_TIMEBOX_MINI_BASE_URL (full URL including scheme).
# Optionally gates on the default-gateway MAC so the hook only fires when
# Claude Code is running on a trusted network.
set -eu
state="${1:-}"
base_url="${CLAUDE_TIMEBOX_MINI_BASE_URL:-}"
allowed="${CLAUDE_TIMEBOX_MINI_ALLOWED_GATEWAYS:-}"

[ -n "$state" ] || exit 0
[ -n "$base_url" ] || exit 0

# Notification filter: Claude Code's Notification hook fires for multiple
# cases (idle_prompt | permission_prompt | elicitation_dialog | auth_success).
# Drop idle_prompt — it fires ~60s after turn end and would clobber the
# /done → clock revert. PermissionRequest and PreToolUse(AskUserQuestion)
# hooks cover the fast-path; Notification stays as a fallback.
if [ "$state" = "waiting" ]; then
    payload=$(cat)
    case "$payload" in
        *'"notification_type":"idle_prompt"'*) exit 0 ;;
        *'"notification_type": "idle_prompt"'*) exit 0 ;;
    esac
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

curl -fsS --max-time 2 "${base_url%/}/${state}" >/dev/null 2>&1 || true
