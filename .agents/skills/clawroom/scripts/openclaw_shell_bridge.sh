#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[openclaw-shell-bridge] %s\n' "$*" >&2
}

process_snapshot() {
  local pid="${1:-$$}"
  ps -o pid= -o ppid= -o pgid= -o sess= -o command= -p "$pid" 2>/dev/null | awk '{$1=$1; print}'
}

log_process_context() {
  local label="${1:-process_context}"
  local self_info parent_info
  self_info="$(process_snapshot "$$")"
  parent_info="$(process_snapshot "$PPID")"
  log "$label self=[$self_info] parent=[$parent_info]"
}

record_reply_generation_diag() {
  local stderr_text="${1:-}"
  if [[ -z "$stderr_text" ]]; then
    return
  fi
  if [[ "$stderr_text" == *"session file locked"* ]]; then
    RUNNER_LAST_ERROR="openclaw_session_file_locked"
    RUNNER_RECOVERY_REASON="session_lock_during_reply_generation"
  elif [[ "$stderr_text" == *"gateway timeout after"* ]]; then
    RUNNER_LAST_ERROR="openclaw_gateway_timeout"
    RUNNER_RECOVERY_REASON="gateway_timeout_during_reply_generation"
  else
    return
  fi
  write_runtime_state
  log "reply_generation_diag recovery_reason=$RUNNER_RECOVERY_REASON last_error=$RUNNER_LAST_ERROR"
}

usage() {
  cat <<'EOF'
Usage:
  openclaw_shell_bridge.sh <JOIN_URL> [options]

Options:
  --agent-id <id>              OpenClaw agent id (default: main)
  --role <auto|initiator|responder>
  --poll-seconds <seconds>     Poll interval (default: 1)
  --max-seconds <seconds>      Max runtime (0 means forever; default: 0)
  --thinking <level>           OpenClaw thinking level (default: minimal)
  --openclaw-mode <mode>       OpenClaw execution mode (gateway|local) (default: gateway)
  --openclaw-timeout <seconds> OpenClaw turn timeout (default: 90)
  --client-name <name>         Join client name (default: OpenClawShellBridge)
  --heartbeat-seconds <sec>    Heartbeat interval (default: 5)
  --start                      Allow initiator kickoff after peer joins
  --print-result               Print room result summary before exit
  --auto-install <on|off>      Auto-install missing dependencies (default: on)
  --preflight-mode <mode>      Accepted for compatibility; ignored

Example:
  bash openclaw_shell_bridge.sh "https://api.clawroom.cc/join/room_abc?token=inv_xyz" \
    --agent-id main --role auto --max-seconds 0 --print-result
EOF
}

AGENT_ID="main"
ROLE="auto"
POLL_SECONDS="1"
MAX_SECONDS="0"
THINKING="minimal"
OPENCLAW_MODE="gateway"
OPENCLAW_TIMEOUT="90"
CLIENT_NAME="OpenClawShellBridge"
HEARTBEAT_SECONDS="5"
ALLOW_START=0
PRINT_RESULT=0
AUTO_INSTALL="on"
RUNNER_LOG_REF="${CLAWROOM_RUNNER_LOG_REF:-}"
SHUTDOWN_REASON="client_exit"
SHUTDOWN_NOTE="client_exit"
SHUTDOWN_LAST_ERROR=""
CURL_CONNECT_TIMEOUT="${CLAWROOM_CURL_CONNECT_TIMEOUT:-5}"
CURL_MAX_TIME="${CLAWROOM_CURL_MAX_TIME:-20}"
CURL_GET_RETRY_COUNT="${CLAWROOM_CURL_GET_RETRY_COUNT:-2}"

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

JOIN_URL="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent-id)
      AGENT_ID="${2:-}"
      shift 2
      ;;
    --role)
      ROLE="${2:-}"
      shift 2
      ;;
    --poll-seconds)
      POLL_SECONDS="${2:-}"
      shift 2
      ;;
    --max-seconds)
      MAX_SECONDS="${2:-}"
      shift 2
      ;;
    --thinking)
      THINKING="${2:-}"
      shift 2
      ;;
    --openclaw-mode)
      OPENCLAW_MODE="${2:-gateway}"
      shift 2
      ;;
    --openclaw-timeout)
      OPENCLAW_TIMEOUT="${2:-}"
      shift 2
      ;;
    --client-name)
      CLIENT_NAME="${2:-}"
      shift 2
      ;;
    --heartbeat-seconds)
      HEARTBEAT_SECONDS="${2:-5}"
      shift 2
      ;;
    --start)
      ALLOW_START=1
      shift
      ;;
    --print-result)
      PRINT_RESULT=1
      shift
      ;;
    --auto-install)
      AUTO_INSTALL="${2:-on}"
      shift 2
      ;;
    --preflight-mode)
      # Compatibility with Python bridge flags.
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log "unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ ! "$ROLE" =~ ^(auto|initiator|responder)$ ]]; then
  log "invalid role: $ROLE"
  exit 2
fi
if [[ ! "$OPENCLAW_MODE" =~ ^(gateway|local)$ ]]; then
  log "invalid openclaw-mode: $OPENCLAW_MODE"
  exit 2
fi
if [[ ! "$HEARTBEAT_SECONDS" =~ ^[0-9]+$ || "$HEARTBEAT_SECONDS" -lt 1 ]]; then
  log "invalid heartbeat-seconds: $HEARTBEAT_SECONDS"
  exit 2
fi

cleanup() {
  :
}

handle_signal() {
  local sig="${1:-TERM}"
  local lower
  lower="$(printf '%s' "$sig" | tr '[:upper:]' '[:lower:]')"
  SHUTDOWN_REASON="signal_${lower}"
  SHUTDOWN_NOTE="signal:${sig}"
  SHUTDOWN_LAST_ERROR="signal:${sig}"
  RUNNER_LAST_ERROR="$SHUTDOWN_LAST_ERROR"
  RUNNER_RECOVERY_REASON="$SHUTDOWN_REASON"
  log "early signal received $sig"
  exit 0
}

trap 'handle_signal TERM' TERM
trap 'handle_signal HUP' HUP
trap 'handle_signal INT' INT
trap cleanup EXIT

if ! command -v jq >/dev/null 2>&1; then
  if [[ "$AUTO_INSTALL" != "on" ]]; then
    log "jq is required"
    exit 1
  fi
fi

PM="none"
if command -v apt-get >/dev/null 2>&1; then
  PM="apt"
elif command -v apk >/dev/null 2>&1; then
  PM="apk"
elif command -v dnf >/dev/null 2>&1; then
  PM="dnf"
elif command -v yum >/dev/null 2>&1; then
  PM="yum"
elif command -v brew >/dev/null 2>&1; then
  PM="brew"
fi

run_privileged() {
  if [[ "$(id -u)" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

APT_UPDATED=0
install_with_os_pm() {
  local pkg="$1"
  case "$PM" in
    apt)
      if [[ "$APT_UPDATED" == "0" ]]; then
        run_privileged apt-get update -y >/dev/null
        APT_UPDATED=1
      fi
      run_privileged apt-get install -y "$pkg" >/dev/null
      ;;
    apk)
      run_privileged apk add --no-cache "$pkg" >/dev/null
      ;;
    dnf)
      run_privileged dnf install -y "$pkg" >/dev/null
      ;;
    yum)
      run_privileged yum install -y "$pkg" >/dev/null
      ;;
    brew)
      brew install "$pkg" >/dev/null
      ;;
    *)
      return 1
      ;;
  esac
}

install_openclaw_cli() {
  if command -v npm >/dev/null 2>&1; then
    log "installing openclaw via npm"
    npm install -g openclaw >/dev/null
    return 0
  fi
  return 1
}

ensure_cmd() {
  local cmd="$1"
  local os_pkg="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$AUTO_INSTALL" != "on" ]]; then
    log "missing dependency: $cmd"
    return 1
  fi
  log "missing dependency: $cmd; attempting install"
  if [[ "$cmd" == "openclaw" ]]; then
    install_openclaw_cli || {
      log "auto-install failed for openclaw"
      return 1
    }
  else
    install_with_os_pm "$os_pkg" || {
      log "auto-install failed for $cmd"
      return 1
    }
  fi
  command -v "$cmd" >/dev/null 2>&1
}

ensure_cmd "curl" "curl"
ensure_cmd "jq" "jq"
ensure_cmd "openclaw" "openclaw"

parse_join_url() {
  local url="$1"
  local scheme rest hostport base room token host

  # Parse base: <scheme>://<host[:port]>
  if [[ "$url" == https://* ]]; then
    scheme="https"
    rest="${url#https://}"
  elif [[ "$url" == http://* ]]; then
    scheme="http"
    rest="${url#http://}"
  else
    log "cannot parse join URL (missing scheme): $url"
    exit 2
  fi
  rest="${rest#//}"
  hostport="${rest%%/*}"
  if [[ -z "$hostport" ]]; then
    log "cannot parse join URL (missing host): $url"
    exit 2
  fi
  base="${scheme}://${hostport}"

  # Parse room id from known forms:
  # - .../join/<room_id>?token=...
  # - .../rooms/<room_id>/join_info?token=...
  room=""
  if [[ "$url" =~ /join/([^/?]+) ]]; then
    room="${BASH_REMATCH[1]}"
  elif [[ "$url" =~ /rooms/([^/]+)/join_info ]]; then
    room="${BASH_REMATCH[1]}"
  fi

  # Parse token from query string (simple substring match).
  token=""
  if [[ "$url" == *"token="* ]]; then
    token="${url#*token=}"
    token="${token%%&*}"
  fi

  if [[ -z "$base" || -z "$room" || -z "$token" ]]; then
    log "cannot parse join URL: $url"
    exit 2
  fi

  # Human-facing clawroom.cc links should still use API host for join/messages/events.
  host="${hostport%%:*}"
  if [[ "$host" == "clawroom.cc" || "$host" == "www.clawroom.cc" ]]; then
    base="${CLAWROOM_API_BASE:-https://api.clawroom.cc}"
    log "rewrote UI host join URL to API base: $base"
  fi

  BASE_URL="$base"
  ROOM_ID="$room"
  INVITE_TOKEN="$token"
}

json_request() {
  local method="$1"
  local url="$2"
  local token="$3"
  local body="${4:-}"
  local args
  args=(
    -sS
    --connect-timeout "$CURL_CONNECT_TIMEOUT"
    --max-time "$CURL_MAX_TIME"
    -X "$method"
    "$url"
    -H "X-Invite-Token: $token"
  )
  if [[ "$method" == "GET" && "$CURL_GET_RETRY_COUNT" =~ ^[0-9]+$ && "$CURL_GET_RETRY_COUNT" -gt 0 ]]; then
    args+=( --retry "$CURL_GET_RETRY_COUNT" --retry-delay 1 --retry-all-errors )
  fi
  if [[ -n "$body" ]]; then
    args+=( -H "content-type: application/json" -d "$body" )
  fi
  curl "${args[@]}"
}

RUNNER_ID=""
ATTEMPT_ID=""
EXECUTION_MODE="managed_attached"
RUNNER_STATUS="pending"
RUNNER_LAST_ERROR=""
RUNNER_RECOVERY_REASON=""
RUNNER_RELEASED=0
RUNNER_STATUS_NOTE=""
RUNNER_PHASE="joined"
RUNNER_PHASE_DETAIL="participant_joined"
RUNTIME_STATE_DIR=""
RUNTIME_STATE_FILE=""
RUNTIME_STATE_ROOT="${CLAWROOM_RUNTIME_STATE_ROOT:-}"
RUNTIME_STATE_EPHEMERAL=1
HEARTBEAT_WATCHDOG_PID=""
MANAGED_CERTIFIED="0"
RECOVERY_POLICY="takeover_only"
RUNNER_CAPABILITIES='{}'

refresh_runner_capabilities() {
  local persistence_supported managed_certified_bool
  persistence_supported=false
  managed_certified_bool=false
  if [[ -n "$RUNTIME_STATE_FILE" ]]; then
    persistence_supported=true
  fi
  if [[ "$MANAGED_CERTIFIED" == "1" ]]; then
    managed_certified_bool=true
  fi
  RUNNER_CAPABILITIES=$(jq -nc \
    --argjson persistence_supported "$persistence_supported" \
    --argjson managed_certified "$managed_certified_bool" \
    --arg recovery_policy "$RECOVERY_POLICY" \
    '{
      strategy:"daemon-safe",
      owner_reply_supported:false,
      background_safe:true,
      persistence_supported:$persistence_supported,
      health_surface:true,
      managed_certified:$managed_certified,
      recovery_policy:$recovery_policy
    }')
}

write_runtime_state() {
  if [[ -z "$RUNTIME_STATE_FILE" ]]; then
    return
  fi
  local tmp_file
  tmp_file="${RUNTIME_STATE_FILE}.tmp.$$"
  jq -nc \
    --arg runner_id "$RUNNER_ID" \
    --arg attempt_id "$ATTEMPT_ID" \
    --arg status "$RUNNER_STATUS" \
    --arg note "$RUNNER_STATUS_NOTE" \
    --arg last_error "$RUNNER_LAST_ERROR" \
    --arg recovery_reason "$RUNNER_RECOVERY_REASON" \
    --arg phase "$RUNNER_PHASE" \
    --arg phase_detail "$RUNNER_PHASE_DETAIL" \
    --arg execution_mode "$EXECUTION_MODE" \
    --arg log_ref "$RUNNER_LOG_REF" \
    --argjson capabilities "$RUNNER_CAPABILITIES" \
    --arg released "$RUNNER_RELEASED" '
      {
        runner_id: $runner_id,
        attempt_id: $attempt_id,
        status: $status,
        note: $note,
        last_error: $last_error,
        recovery_reason: $recovery_reason,
        phase: $phase,
        phase_detail: $phase_detail,
        execution_mode: $execution_mode,
        log_ref: $log_ref,
        capabilities: $capabilities,
        released: ($released == "1")
      }
    ' >"$tmp_file" && mv "$tmp_file" "$RUNTIME_STATE_FILE"
}

runner_renew_from_state() {
  local state_json="$1"
  if [[ -z "$RUNNER_ID" || "$RUNNER_RELEASED" == "1" ]]; then
    return 0
  fi
  local lease_seconds payload status last_error recovery_reason phase phase_detail attempt_id log_ref execution_mode capabilities
  lease_seconds=$(( HEARTBEAT_SECONDS * 3 > 30 ? HEARTBEAT_SECONDS * 3 : 30 ))
  status=$(printf '%s' "$state_json" | jq -r '.status // "active"')
  last_error=$(printf '%s' "$state_json" | jq -r '.last_error // ""')
  recovery_reason=$(printf '%s' "$state_json" | jq -r '.recovery_reason // ""')
  phase=$(printf '%s' "$state_json" | jq -r '.phase // ""')
  phase_detail=$(printf '%s' "$state_json" | jq -r '.phase_detail // ""')
  attempt_id=$(printf '%s' "$state_json" | jq -r '.attempt_id // ""')
  log_ref=$(printf '%s' "$state_json" | jq -r '.log_ref // ""')
  execution_mode=$(printf '%s' "$state_json" | jq -r '.execution_mode // "managed_attached"')
  capabilities=$(printf '%s' "$state_json" | jq -c '.capabilities // {}')
  payload=$(jq -nc \
    --arg runner_id "$RUNNER_ID" \
    --arg status "$status" \
    --arg execution_mode "$execution_mode" \
    --argjson capabilities "$capabilities" \
    --argjson lease_seconds "$lease_seconds" \
    --arg log_ref "$log_ref" \
    --arg last_error "$last_error" \
    --arg recovery_reason "$recovery_reason" \
    --arg phase "$phase" \
    --arg phase_detail "$phase_detail" \
    --arg attempt_id "$attempt_id" '
      {
        runner_id: $runner_id,
        status: $status,
        execution_mode: $execution_mode,
        capabilities: $capabilities,
        lease_seconds: $lease_seconds,
        managed_certified: ($capabilities.managed_certified // false),
        recovery_policy: ($capabilities.recovery_policy // "takeover_only")
      }
      + (if ($log_ref|length) > 0 then {log_ref:$log_ref} else {} end)
      + (if ($last_error|length) > 0 then {last_error:$last_error} else {} end)
      + (if ($recovery_reason|length) > 0 then {recovery_reason:$recovery_reason} else {} end)
      + (if ($phase|length) > 0 then {phase:$phase} else {} end)
      + (if ($phase_detail|length) > 0 then {phase_detail:$phase_detail} else {} end)
      + (if ($attempt_id|length) > 0 then {attempt_id:$attempt_id} else {} end)
    ')
  if ! json_request "POST" "$BASE_URL/rooms/$ROOM_ID/runner/renew" "$API_TOKEN" "$payload" >/dev/null 2>&1; then
    local phase phase_detail
    phase=$(printf '%s' "$state_json" | jq -r '.phase // ""')
    phase_detail=$(printf '%s' "$state_json" | jq -r '.phase_detail // ""')
    log "watchdog renew failed phase=${phase:-unknown} detail=${phase_detail:-unknown}"
    return 1
  fi
}

heartbeat_watchdog_loop() {
  while true; do
    sleep "$HEARTBEAT_SECONDS"
    if [[ -n "$HEARTBEAT_WATCHDOG_PID" && "$HEARTBEAT_WATCHDOG_PID" != "$$" ]]; then
      :
    fi
    local state_json released
    if [[ -n "$RUNTIME_STATE_FILE" && -f "$RUNTIME_STATE_FILE" ]]; then
      state_json=$(cat "$RUNTIME_STATE_FILE" 2>/dev/null || printf '{}')
    else
      state_json='{}'
    fi
    released=$(printf '%s' "$state_json" | jq -r '.released // false')
    if [[ "$released" == "true" ]]; then
      break
    fi
    if json_request "POST" "$BASE_URL/rooms/$ROOM_ID/heartbeat" "$API_TOKEN" '{}' >/dev/null 2>&1; then
      LAST_HEARTBEAT_TS=$(date +%s)
      if ! runner_renew_from_state "$state_json" 2>/dev/null; then
        log "watchdog renew failed (non-fatal)"
      fi
    else
      log "watchdog heartbeat failed (non-fatal)"
    fi
  done
}

start_heartbeat_watchdog() {
  if [[ -n "$HEARTBEAT_WATCHDOG_PID" ]]; then
    return
  fi
  heartbeat_watchdog_loop &
  HEARTBEAT_WATCHDOG_PID=$!
  log "heartbeat watchdog started pid=$HEARTBEAT_WATCHDOG_PID"
}

stop_heartbeat_watchdog() {
  if [[ -z "$HEARTBEAT_WATCHDOG_PID" ]]; then
    return
  fi
  kill "$HEARTBEAT_WATCHDOG_PID" >/dev/null 2>&1 || true
  wait "$HEARTBEAT_WATCHDOG_PID" >/dev/null 2>&1 || true
  HEARTBEAT_WATCHDOG_PID=""
}

runner_claim() {
  if [[ -z "$RUNNER_ID" ]]; then
    return
  fi
  local payload resp attempt_id lease_seconds
  lease_seconds=$(( HEARTBEAT_SECONDS * 3 > 30 ? HEARTBEAT_SECONDS * 3 : 30 ))
  payload=$(jq -nc \
    --arg runner_id "$RUNNER_ID" \
    --arg execution_mode "$EXECUTION_MODE" \
    --arg status "$RUNNER_STATUS" \
    --argjson capabilities "$RUNNER_CAPABILITIES" \
    --argjson lease_seconds "$lease_seconds" \
    --arg log_ref "$RUNNER_LOG_REF" \
    --arg last_error "$RUNNER_LAST_ERROR" \
    --arg recovery_reason "$RUNNER_RECOVERY_REASON" \
    --arg phase "$RUNNER_PHASE" \
    --arg phase_detail "$RUNNER_PHASE_DETAIL" \
    --arg attempt_id "$ATTEMPT_ID" '
      {
        runner_id: $runner_id,
        execution_mode: $execution_mode,
        status: $status,
        capabilities: $capabilities,
        lease_seconds: $lease_seconds,
        managed_certified: $capabilities.managed_certified,
        recovery_policy: ($capabilities.recovery_policy // "takeover_only")
      }
      + (if ($log_ref|length) > 0 then {log_ref:$log_ref} else {} end)
      + (if ($last_error|length) > 0 then {last_error:$last_error} else {} end)
      + (if ($recovery_reason|length) > 0 then {recovery_reason:$recovery_reason} else {} end)
      + (if ($phase|length) > 0 then {phase:$phase} else {} end)
      + (if ($phase_detail|length) > 0 then {phase_detail:$phase_detail} else {} end)
      + (if ($attempt_id|length) > 0 then {attempt_id:$attempt_id} else {} end)
    ')
  if resp=$(json_request "POST" "$BASE_URL/rooms/$ROOM_ID/runner/claim" "$API_TOKEN" "$payload" 2>/dev/null); then
    attempt_id=$(printf '%s' "$resp" | jq -r '.attempt_id // ""')
    if [[ -n "$attempt_id" && "$attempt_id" != "null" ]]; then
      ATTEMPT_ID="$attempt_id"
    fi
    write_runtime_state
  else
    log "runner claim failed (non-fatal)"
  fi
}

runner_renew() {
  if [[ -z "$RUNNER_ID" || "$RUNNER_RELEASED" == "1" ]]; then
    return
  fi
  local payload resp attempt_id lease_seconds
  lease_seconds=$(( HEARTBEAT_SECONDS * 3 > 30 ? HEARTBEAT_SECONDS * 3 : 30 ))
  payload=$(jq -nc \
    --arg runner_id "$RUNNER_ID" \
    --arg execution_mode "$EXECUTION_MODE" \
    --arg status "$RUNNER_STATUS" \
    --argjson capabilities "$RUNNER_CAPABILITIES" \
    --argjson lease_seconds "$lease_seconds" \
    --arg log_ref "$RUNNER_LOG_REF" \
    --arg last_error "$RUNNER_LAST_ERROR" \
    --arg recovery_reason "$RUNNER_RECOVERY_REASON" \
    --arg phase "$RUNNER_PHASE" \
    --arg phase_detail "$RUNNER_PHASE_DETAIL" \
    --arg attempt_id "$ATTEMPT_ID" '
      {
        runner_id: $runner_id,
        status: $status,
        execution_mode: $execution_mode,
        capabilities: $capabilities,
        lease_seconds: $lease_seconds,
        managed_certified: $capabilities.managed_certified,
        recovery_policy: ($capabilities.recovery_policy // "takeover_only")
      }
      + (if ($log_ref|length) > 0 then {log_ref:$log_ref} else {} end)
      + (if ($last_error|length) > 0 then {last_error:$last_error} else {} end)
      + (if ($recovery_reason|length) > 0 then {recovery_reason:$recovery_reason} else {} end)
      + (if ($phase|length) > 0 then {phase:$phase} else {} end)
      + (if ($phase_detail|length) > 0 then {phase_detail:$phase_detail} else {} end)
      + (if ($attempt_id|length) > 0 then {attempt_id:$attempt_id} else {} end)
    ')
  if resp=$(json_request "POST" "$BASE_URL/rooms/$ROOM_ID/runner/renew" "$API_TOKEN" "$payload" 2>/dev/null); then
    attempt_id=$(printf '%s' "$resp" | jq -r '.attempt_id // ""')
    if [[ -n "$attempt_id" && "$attempt_id" != "null" ]]; then
      ATTEMPT_ID="$attempt_id"
    fi
    write_runtime_state
  else
    log "runner renew failed (non-fatal)"
  fi
}

runner_release() {
  local status="${1:-exited}"
  local reason="${2:-}"
  local last_error="${3:-$RUNNER_LAST_ERROR}"
  if [[ -z "$RUNNER_ID" || "$RUNNER_RELEASED" == "1" ]]; then
    return
  fi
  local payload
  payload=$(jq -nc \
    --arg runner_id "$RUNNER_ID" \
    --arg status "$status" \
    --arg reason "$reason" \
    --arg last_error "$last_error" \
    --arg attempt_id "$ATTEMPT_ID" '
      {
        runner_id: $runner_id,
        status: $status
      }
      + (if ($reason|length) > 0 then {reason:$reason} else {} end)
      + (if ($last_error|length) > 0 then {last_error:$last_error} else {} end)
      + (if ($attempt_id|length) > 0 then {attempt_id:$attempt_id} else {} end)
    ')
  if json_request "POST" "$BASE_URL/rooms/$ROOM_ID/runner/release" "$API_TOKEN" "$payload" >/dev/null 2>&1; then
    RUNNER_RELEASED=1
    write_runtime_state
  else
    log "runner release failed (non-fatal)"
  fi
}

runner_set_status() {
  local status="$1"
  local note="${2:-}"
  local last_error="${3:-}"
  local recovery_reason="${4:-}"
  local phase="${5:-}"
  local phase_detail="${6:-}"
  local changed=0
  if [[ "$RUNNER_STATUS" != "$status" ]]; then
    RUNNER_STATUS="$status"
    changed=1
  fi
  if [[ "$RUNNER_STATUS_NOTE" != "$note" ]]; then
    RUNNER_STATUS_NOTE="$note"
    changed=1
  fi
  if [[ "$RUNNER_LAST_ERROR" != "$last_error" ]]; then
    RUNNER_LAST_ERROR="$last_error"
    changed=1
  fi
  if [[ "$RUNNER_RECOVERY_REASON" != "$recovery_reason" ]]; then
    RUNNER_RECOVERY_REASON="$recovery_reason"
    changed=1
  fi
  if [[ -n "$phase" && "$RUNNER_PHASE" != "$phase" ]]; then
    RUNNER_PHASE="$phase"
    changed=1
  fi
  if [[ -n "$phase_detail" && "$RUNNER_PHASE_DETAIL" != "$phase_detail" ]]; then
    RUNNER_PHASE_DETAIL="$phase_detail"
    changed=1
  fi
  if [[ "$changed" == "1" ]]; then
    write_runtime_state
    runner_renew
  fi
}

extract_first_json_object() {
  local text="$1"
  local compact prefix suffix start end len
  compact=$(printf '%s' "$text" | tr '\r\n' ' ')
  if [[ "$compact" != *"{"* || "$compact" != *"}"* ]]; then
    return 0
  fi
  prefix="${compact%%\{*}"
  suffix="${compact##*\}}"
  start="${#prefix}"
  end=$(( ${#compact} - ${#suffix} - 1 ))
  len=$(( end - start + 1 ))
  if [[ "$len" -le 0 ]]; then
    return 0
  fi
  printf '%s' "${compact:$start:$len}"
}

normalize_outgoing() {
  local raw="$1"
  jq -cn --argjson raw "$raw" '
    def clean_obj(v): if (v|type) == "object" then v else {} end;
    def clean_arr(v): if (v|type) == "array" then [v[] | tostring | gsub("^\\s+|\\s+$"; "") | select(length > 0)] else [] end;

    (($raw.intent // "ANSWER") | tostring | ascii_upcase) as $intent0
    | (if $intent0 == "NEED_HUMAN" then "ASK_OWNER" else $intent0 end) as $intent1
    | (if ["ASK","ANSWER","NOTE","DONE","ASK_OWNER","OWNER_REPLY"] | index($intent1) then $intent1 else "ANSWER" end) as $intent
    | {
        intent: $intent,
        text: (($raw.text // "(no text)") | tostring),
        fills: (clean_obj($raw.fills) | with_entries(select((.key|tostring|length) > 0 and (.value|tostring|length) > 0) | .key |= tostring | .value |= tostring)),
        facts: clean_arr($raw.facts),
        questions: clean_arr($raw.questions),
        expect_reply: (
          if $intent == "ASK" then true
          elif $intent == "DONE" then false
          elif $intent == "ASK_OWNER" then false
          elif $intent == "NOTE" then false
          else
            (if ($raw.expect_reply|type) == "boolean" then $raw.expect_reply else true end)
          end
        ),
        meta: clean_obj($raw.meta)
      }
  '
}

room_prompt() {
  local role="$1"
  local room_json="$2"
  local latest_event_json="${3:-}"
  local started="$4"

  local topic goal required known status stop_reason
  topic=$(printf '%s' "$room_json" | jq -r '.topic // ""')
  goal=$(printf '%s' "$room_json" | jq -r '.goal // ""')
  required=$(printf '%s' "$room_json" | jq -c '.required_fields // []')
  known=$(printf '%s' "$room_json" | jq -c '.fields // {}')
  status=$(printf '%s' "$room_json" | jq -r '.status // "active"')
  stop_reason=$(printf '%s' "$room_json" | jq -r '.stop_reason // ""')

  local incoming
  incoming="No incoming relay message yet."
  if [[ -n "$latest_event_json" ]]; then
    local from intent text fills
    from=$(printf '%s' "$latest_event_json" | jq -r '.payload.from // .payload.message.sender // ""')
    intent=$(printf '%s' "$latest_event_json" | jq -r '.payload.message.intent // ""')
    text=$(printf '%s' "$latest_event_json" | jq -r '.payload.message.text // ""')
    fills=$(printf '%s' "$latest_event_json" | jq -c '.payload.message.fills // {}')
    incoming=$(cat <<EOF
Incoming relay message:
- from: $from
- intent: $intent
- text: $text
- fills: $fills
EOF
)
  fi

  local role_hint starter
  if [[ "$role" == "initiator" ]]; then
    role_hint="You are the initiating product-side agent. Ask concise questions and drive to a concrete decision."
    starter=""
    if [[ "$started" == "0" ]]; then
      starter="This is room start. Initiate with ASK only after peer has joined."
    fi
  else
    role_hint="You are the responding partner-side agent. Answer directly and help converge quickly."
    starter=""
  fi

  cat <<EOF
You are acting as an OpenClaw participant in a machine-to-machine room.
Return ONLY a single JSON object and nothing else.

Role: $role
Role guidance: $role_hint

Room topic: $topic
Room goal: $goal
Required fields: $required
Known fields: $known
Room status: $status stop_reason=$stop_reason

$incoming

$starter

Output schema (all keys required):
{"intent":"ASK|ANSWER|NOTE|DONE|ASK_OWNER|OWNER_REPLY","text":"short message","fills":{},"facts":[],"questions":[],"expect_reply":true,"meta":{}}

Rules:
- Keep text under 160 words.
- If no further reply is needed, use DONE and expect_reply=false.
- If owner input is needed, use ASK_OWNER (it will be converted to ASK in autonomous mode).
EOF
}

ask_openclaw() {
  local prompt="$1"
  local response payload_text raw_json outgoing retry_prompt started_at elapsed stderr_file rc stderr_text
  local cmd=(openclaw agent --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
  if [[ "$OPENCLAW_MODE" == "local" ]]; then
    cmd=(openclaw agent --local --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
  fi
  started_at=$(date +%s)
  log_process_context "reply_generation_process_context"
  log "reply_generation_start mode=$OPENCLAW_MODE session=$SESSION_ID"
  stderr_file="$(mktemp 2>/dev/null || true)"
  if [[ -z "$stderr_file" ]]; then
    stderr_file="/tmp/clawroom-openclaw-stderr-${ROOM_ID}-$$.log"
  fi
  set +e
  response=$("${cmd[@]}" 2>"$stderr_file")
  rc=$?
  set -e
  elapsed=$(( $(date +%s) - started_at ))
  stderr_text="$(tr '\n' ' ' <"$stderr_file" 2>/dev/null | cut -c1-220)"
  rm -f "$stderr_file" >/dev/null 2>&1 || true
  log "reply_generation_finish seconds=$elapsed rc=$rc mode=$OPENCLAW_MODE session=$SESSION_ID stderr=$stderr_text"
  record_reply_generation_diag "$stderr_text"
  if [[ "$rc" -ne 0 && "$stderr_text" == *"session file locked"* ]]; then
    local old_session="$SESSION_ID"
    SESSION_ID=$(hash_sha256 "$(printf 'clawroom-v3-retry:%s:%s:%s:%s' "$ROOM_ID" "$AGENT_ID" "$PARTICIPANT" "$(date +%s)")")
    log "session_lock_retry old=$old_session new=$SESSION_ID"
    cmd=(openclaw agent --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
    if [[ "$OPENCLAW_MODE" == "local" ]]; then
      cmd=(openclaw agent --local --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
    fi
    started_at=$(date +%s)
    stderr_file="$(mktemp 2>/dev/null || true)"
    if [[ -z "$stderr_file" ]]; then
      stderr_file="/tmp/clawroom-openclaw-stderr-${ROOM_ID}-session-retry-$$.log"
    fi
    set +e
    response=$("${cmd[@]}" 2>"$stderr_file")
    rc=$?
    set -e
    elapsed=$(( $(date +%s) - started_at ))
    stderr_text="$(tr '\n' ' ' <"$stderr_file" 2>/dev/null | cut -c1-220)"
    rm -f "$stderr_file" >/dev/null 2>&1 || true
    log "reply_generation_session_retry_finish seconds=$elapsed rc=$rc mode=$OPENCLAW_MODE session=$SESSION_ID stderr=$stderr_text"
    record_reply_generation_diag "$stderr_text"
  fi
  if [[ "$rc" -ne 0 ]]; then
    log "openclaw command failed; falling back to NOTE"
    jq -nc --arg txt "OpenClaw command failed during reply generation; continuing with a safe note." --arg err "$stderr_text" '{intent:"NOTE",text:$txt,fills:{},facts:[],questions:[],expect_reply:false,meta:{fallback:true,openclaw_error:$err}}'
    return
  fi
  # OpenClaw --json output differs by mode/version:
  # - --local: { payloads: [...] }
  # - gateway: { result: { payloads: [...] } }
  payload_text=$(printf '%s' "$response" | jq -r '[
    ((.payloads // .result.payloads // [])[]? | .text? // empty)
  ] | join("\n")')
  raw_json=$(extract_first_json_object "$payload_text")

  if [[ -z "$raw_json" ]]; then
    log "openclaw returned no JSON; retrying once with strict JSON reminder"
    retry_prompt=$(cat <<EOF
Your previous reply could not be parsed as JSON.
Return ONLY one raw JSON object (no markdown fences, no prose) matching this schema exactly:
{"intent":"ASK|ANSWER|NOTE|DONE|ASK_OWNER|OWNER_REPLY","text":"short message","fills":{},"facts":[],"questions":[],"expect_reply":true,"meta":{}}

Re-answer the same task below:

$prompt
EOF
)
    cmd=(openclaw agent --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$retry_prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
    if [[ "$OPENCLAW_MODE" == "local" ]]; then
      cmd=(openclaw agent --local --json --agent "$AGENT_ID" --session-id "$SESSION_ID" --message "$retry_prompt" --timeout "$OPENCLAW_TIMEOUT" --thinking "$THINKING")
    fi
    started_at=$(date +%s)
    stderr_file="$(mktemp 2>/dev/null || true)"
    if [[ -z "$stderr_file" ]]; then
      stderr_file="/tmp/clawroom-openclaw-stderr-${ROOM_ID}-retry-$$.log"
    fi
    set +e
    response=$("${cmd[@]}" 2>"$stderr_file")
    rc=$?
    set -e
    elapsed=$(( $(date +%s) - started_at ))
    stderr_text="$(tr '\n' ' ' <"$stderr_file" 2>/dev/null | cut -c1-220)"
    rm -f "$stderr_file" >/dev/null 2>&1 || true
    log "reply_generation_retry_finish seconds=$elapsed rc=$rc mode=$OPENCLAW_MODE session=$SESSION_ID stderr=$stderr_text"
    record_reply_generation_diag "$stderr_text"
    if [[ "$rc" -ne 0 ]]; then
      log "openclaw retry command failed; falling back to NOTE"
      jq -nc --arg txt "OpenClaw retry failed during reply generation; continuing with a safe note." --arg err "$stderr_text" '{intent:"NOTE",text:$txt,fills:{},facts:[],questions:[],expect_reply:false,meta:{fallback:true,openclaw_retry_error:$err}}'
      return
    fi
    payload_text=$(printf '%s' "$response" | jq -r '[
      ((.payloads // .result.payloads // [])[]? | .text? // empty)
    ] | join("\n")')
    raw_json=$(extract_first_json_object "$payload_text")
  fi

  if [[ -z "$raw_json" ]]; then
    log "openclaw returned no JSON; falling back to NOTE"
    # IMPORTANT: do not request a reply on fallback; otherwise two bridges can
    # ping-pong NOTE messages and burn the entire room turn_limit.
    jq -nc --arg txt "Unable to parse agent JSON response; continuing with a safe note." '{intent:"NOTE",text:$txt,fills:{},facts:[],questions:[],expect_reply:false,meta:{fallback:true}}'
    return
  fi

  outgoing=$(normalize_outgoing "$raw_json")
  if [[ "$(printf '%s' "$outgoing" | jq -r '.intent')" == "ASK_OWNER" ]]; then
    outgoing=$(printf '%s' "$outgoing" | jq '.intent="ASK" | .expect_reply=true | .meta.owner_unavailable=true | .meta.converted_from="ASK_OWNER"')
  fi
  printf '%s' "$outgoing"
}

hash_sha256() {
  local input="$1"
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$input" | shasum -a 256 | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$input" | sha256sum | awk '{print $1}'
    return
  fi
  if command -v openssl >/dev/null 2>&1; then
    printf '%s' "$input" | openssl dgst -sha256 | awk '{print $NF}'
    return
  fi
  log "missing hash tool: need one of shasum/sha256sum/openssl"
  exit 1
}

parse_join_url "$JOIN_URL"

log "parsed join URL -> base=$BASE_URL room=$ROOM_ID"
API_TOKEN="$INVITE_TOKEN"

JOIN_BODY=$(jq -nc --arg name "$CLIENT_NAME" '{client_name:$name}')
log_process_context "bridge_process_context"
JOIN_RESP=$(json_request "POST" "$BASE_URL/rooms/$ROOM_ID/join" "$INVITE_TOKEN" "$JOIN_BODY")
PARTICIPANT=$(printf '%s' "$JOIN_RESP" | jq -r '.participant')
PARTICIPANT_TOKEN=$(printf '%s' "$JOIN_RESP" | jq -r '.participant_token // empty')
ROOM_JSON=$(printf '%s' "$JOIN_RESP" | jq -c '.room')

if [[ "$PARTICIPANT" == "null" || -z "$PARTICIPANT" ]]; then
  log "join failed: no participant in response"
  exit 1
fi

if [[ -n "$PARTICIPANT_TOKEN" ]]; then
  API_TOKEN="$PARTICIPANT_TOKEN"
  log "switched to participant session token"
fi

if [[ -n "$RUNTIME_STATE_ROOT" ]]; then
  RUNTIME_STATE_DIR="${RUNTIME_STATE_ROOT%/}/$ROOM_ID/$PARTICIPANT"
  mkdir -p "$RUNTIME_STATE_DIR"
  RUNTIME_STATE_EPHEMERAL=0
else
  RUNTIME_STATE_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t clawroom-shell-bridge)"
  RUNTIME_STATE_EPHEMERAL=1
fi
RUNTIME_STATE_FILE="$RUNTIME_STATE_DIR/runtime-state.json"

if [[ "${CLAWROOM_MANAGED_CERTIFY:-}" == "1" || "${CLAWROOM_MANAGED_CERTIFY:-}" == "true" || "${CLAWROOM_MANAGED_CERTIFY:-}" == "on" ]]; then
  if [[ "$AGENT_ID" != "main" && -n "$PARTICIPANT_TOKEN" && "$OPENCLAW_MODE" == "gateway" ]]; then
    MANAGED_CERTIFIED="1"
    RECOVERY_POLICY="automatic"
    log "managed certification enabled for dedicated relay agent"
  else
    log "managed certification requested but requirements not met (agent_id=$AGENT_ID token_present=$([[ -n \"$PARTICIPANT_TOKEN\" ]] && echo yes || echo no) mode=$OPENCLAW_MODE)"
  fi
fi
refresh_runner_capabilities

if [[ "$ROLE" == "auto" ]]; then
  TURN_COUNT=$(printf '%s' "$ROOM_JSON" | jq -r '.turn_count // 0')
  JOINED_COUNT=$(printf '%s' "$ROOM_JSON" | jq '[.participants[]? | select(.joined==true)] | length')
  if [[ "$TURN_COUNT" == "0" && "$JOINED_COUNT" -le 1 ]]; then
    ROLE="initiator"
    ALLOW_START=1
    log "auto-detected role: initiator"
  else
    ROLE="responder"
    log "auto-detected role: responder"
  fi
fi

if [[ "$ROLE" == "initiator" ]]; then
  ALLOW_START=1
fi

LAST_HEARTBEAT_TS=0
send_heartbeat_if_due() {
  local now_ts
  now_ts=$(date +%s)
  if [[ "$LAST_HEARTBEAT_TS" != "0" ]]; then
    local delta
    delta=$((now_ts - LAST_HEARTBEAT_TS))
    if [[ "$delta" -lt "$HEARTBEAT_SECONDS" ]]; then
      return
    fi
  fi
  if json_request "POST" "$BASE_URL/rooms/$ROOM_ID/heartbeat" "$API_TOKEN" '{}' >/dev/null 2>&1; then
    LAST_HEARTBEAT_TS="$now_ts"
    runner_renew
  else
    RUNNER_LAST_ERROR="heartbeat_failed"
    log "heartbeat failed (non-fatal)"
  fi
}

cleanup() {
  write_runtime_state
  stop_heartbeat_watchdog
  local release_reason="$SHUTDOWN_REASON"
  local release_last_error="${SHUTDOWN_LAST_ERROR:-$RUNNER_LAST_ERROR}"
  if [[ "$release_reason" == "client_exit" && -n "${STOP_REASON_AFTER:-}" ]]; then
    release_reason="room_closed:${STOP_REASON_AFTER}"
  elif [[ "$release_reason" == "client_exit" && -n "${STOP_REASON:-}" ]]; then
    release_reason="room_closed:${STOP_REASON}"
  fi
  runner_release "exited" "$release_reason" "$release_last_error"

  if [[ "$PRINT_RESULT" == "1" ]]; then
    if RESULT_RESP=$(json_request "GET" "$BASE_URL/rooms/$ROOM_ID/result" "$API_TOKEN" "" 2>/dev/null); then
      SUMMARY=$(printf '%s' "$RESULT_RESP" | jq -r '.result.summary // empty')
      if [[ -n "$SUMMARY" ]]; then
        log "result_summary $SUMMARY"
      fi
    fi
  fi

  json_request "POST" "$BASE_URL/rooms/$ROOM_ID/leave" "$API_TOKEN" "$(jq -nc --arg reason "$release_reason" '{reason:$reason}')" >/dev/null 2>&1 || true
  if [[ -n "$RUNTIME_STATE_DIR" && "$RUNTIME_STATE_EPHEMERAL" == "1" ]]; then
    rm -rf "$RUNTIME_STATE_DIR" >/dev/null 2>&1 || true
  fi
  log "left room=$ROOM_ID"
}

handle_signal() {
  local sig="${1:-TERM}"
  local lower
  lower="$(printf '%s' "$sig" | tr '[:upper:]' '[:lower:]')"
  SHUTDOWN_REASON="signal_${lower}"
  SHUTDOWN_NOTE="signal:${sig}"
  SHUTDOWN_LAST_ERROR="signal:${sig}"
  RUNNER_LAST_ERROR="$SHUTDOWN_LAST_ERROR"
  RUNNER_RECOVERY_REASON="$SHUTDOWN_REASON"
  log_process_context "signal_process_context"
  log "signal received $sig"
  exit 0
}

trap 'handle_signal TERM' TERM
trap 'handle_signal HUP' HUP
trap 'handle_signal INT' INT
trap cleanup EXIT

log "joined participant=$PARTICIPANT role=$ROLE room=$ROOM_ID"
RUNNER_ID="shell:${AGENT_ID}:${PARTICIPANT}:$(hash_sha256 "$(printf 'runner:%s:%s:%s' "$ROOM_ID" "$AGENT_ID" "$PARTICIPANT")" | cut -c1-12)"
RUNNER_STATUS="ready"
write_runtime_state
runner_claim
send_heartbeat_if_due
runner_set_status "ready" "poll_ready" "" "" "event_polling" "poll_ready"
start_heartbeat_watchdog

SESSION_ID=$(hash_sha256 "$(printf 'clawroom-v3:%s:%s:%s' "$ROOM_ID" "$AGENT_ID" "$PARTICIPANT")")

CURSOR=0
SEEN_RELAY_IDS=""
STARTED_MESSAGE_SENT=0
KICKOFF_WAIT_LOGGED=0
START_TS=$(date +%s)
LAST_OBS_JOINED_COUNT=-1
LAST_OBS_TURN_COUNT=-1

is_seen_relay() {
  local id="$1"
  [[ " $SEEN_RELAY_IDS " == *" $id "* ]]
}

mark_seen_relay() {
  local id="$1"
  SEEN_RELAY_IDS="$SEEN_RELAY_IDS $id"
}

send_message() {
  local payload="$1"
  local why="$2"
  local in_reply_to_event_id="${3:-}"
  if [[ -n "$in_reply_to_event_id" && "$in_reply_to_event_id" =~ ^[0-9]+$ && "$in_reply_to_event_id" -gt 0 ]]; then
    payload=$(printf '%s' "$payload" | jq --argjson rid "$in_reply_to_event_id" '.meta = (.meta // {}) | .meta.in_reply_to_event_id = $rid')
  fi
  local intent text
  intent=$(printf '%s' "$payload" | jq -r '.intent')
  text=$(printf '%s' "$payload" | jq -r '.text' | cut -c1-180)
  RUNNER_LAST_ERROR=""
  RUNNER_RECOVERY_REASON=""
  runner_set_status "active" "$why" "" "" "reply_sending" "$why"
  log "send why=$why intent=$intent text=$text"
  local resp
  resp=$(json_request "POST" "$BASE_URL/rooms/$ROOM_ID/messages" "$API_TOKEN" "$payload")
  STARTED_MESSAGE_SENT=1
  runner_set_status "active" "$why" "" "" "reply_sent" "$why"
  local trigger
  trigger=$(printf '%s' "$resp" | jq -r '.host_decision.trigger // empty')
  if [[ -n "$trigger" ]]; then
    log "host_trigger $trigger"
  fi
}

while true; do
  if [[ "$MAX_SECONDS" != "0" ]]; then
    NOW_TS=$(date +%s)
    ELAPSED=$((NOW_TS - START_TS))
    if [[ "$ELAPSED" -ge "$MAX_SECONDS" ]]; then
      SHUTDOWN_REASON="max_seconds_reached"
      SHUTDOWN_NOTE="max_seconds_reached"
      log "max-seconds reached"
      break
    fi
  fi

  send_heartbeat_if_due
  POLL_STARTED_TS=$(date +%s)
  BATCH=$(json_request "GET" "$BASE_URL/rooms/$ROOM_ID/events?after=$CURSOR&limit=200" "$API_TOKEN" "")
  POLL_ELAPSED=$(( $(date +%s) - POLL_STARTED_TS ))
  if [[ "$POLL_ELAPSED" -ge 3 ]]; then
    log "events poll slow seconds=$POLL_ELAPSED cursor=$CURSOR"
  fi
  if ! printf '%s' "$BATCH" | jq -e . >/dev/null 2>&1; then
    log "events poll returned non-JSON; retrying"
    sleep "$POLL_SECONDS"
    continue
  fi

  ROOM_JSON=$(printf '%s' "$BATCH" | jq -c '.room // null')
  if [[ "$ROOM_JSON" == "null" ]]; then
    ERR=$(printf '%s' "$BATCH" | jq -r '.error? // .message? // "unknown_error"')
    log "events poll error: $ERR; retrying"
    sleep "$POLL_SECONDS"
    continue
  fi

  BATCH_NEXT_CURSOR=$(printf '%s' "$BATCH" | jq -r '.next_cursor // empty')
  if [[ -z "$BATCH_NEXT_CURSOR" || ! "$BATCH_NEXT_CURSOR" =~ ^[0-9]+$ ]]; then
    BATCH_NEXT_CURSOR="$CURSOR"
  fi

  ROOM_STATUS=$(printf '%s' "$ROOM_JSON" | jq -r '.status // "active"')
  OBS_JOINED_COUNT=$(printf '%s' "$ROOM_JSON" | jq '[.participants[]? | select(.joined==true)] | length')
  OBS_TURN_COUNT=$(printf '%s' "$ROOM_JSON" | jq -r '.turn_count // 0')
  if [[ "$OBS_JOINED_COUNT" != "$LAST_OBS_JOINED_COUNT" || "$OBS_TURN_COUNT" != "$LAST_OBS_TURN_COUNT" ]]; then
    log "room observation joined=$OBS_JOINED_COUNT turn_count=$OBS_TURN_COUNT cursor=$CURSOR"
    LAST_OBS_JOINED_COUNT="$OBS_JOINED_COUNT"
    LAST_OBS_TURN_COUNT="$OBS_TURN_COUNT"
  fi

  if [[ "$ROOM_STATUS" != "active" ]]; then
    STOP_REASON=$(printf '%s' "$ROOM_JSON" | jq -r '.stop_reason // ""')
    runner_release "exited" "room_closed:${STOP_REASON}"
    log "room ended status=$ROOM_STATUS reason=$STOP_REASON"
    break
  fi

  if [[ "$ROLE" == "initiator" && "$ALLOW_START" == "1" && "$STARTED_MESSAGE_SENT" == "0" ]]; then
    TURN_COUNT=$(printf '%s' "$ROOM_JSON" | jq -r '.turn_count // 0')
    JOINED_COUNT=$(printf '%s' "$ROOM_JSON" | jq '[.participants[]? | select(.joined==true)] | length')
    if [[ "$TURN_COUNT" == "0" && "$JOINED_COUNT" -ge 2 ]]; then
      START_PROMPT=$(room_prompt "$ROLE" "$ROOM_JSON" "" "$STARTED_MESSAGE_SENT")
      OUTGOING=$(ask_openclaw "$START_PROMPT")
      send_message "$OUTGOING" "room_start"
      CURSOR="$BATCH_NEXT_CURSOR"
      sleep "$POLL_SECONDS"
      continue
    fi
    if [[ "$KICKOFF_WAIT_LOGGED" == "0" ]]; then
      runner_set_status "idle" "waiting_for_peer_join" "" "waiting_for_peer_join" "waiting_for_peer_join" "initiator_waiting_for_peer"
      log "waiting for peer join before initiator kickoff"
      KICKOFF_WAIT_LOGGED=1
    fi
  fi

  # Avoid bash process substitution (< <(...)) so the script works in minimal
  # environments where /dev/fd is not available.
  RELAY_TMP="$(mktemp 2>/dev/null || true)"
  if [[ -z "$RELAY_TMP" ]]; then
    RELAY_TMP="/tmp/clawroom-relay-${ROOM_ID}-${CURSOR}-$$.jsonl"
  fi
  if ! printf '%s' "$BATCH" | jq -c '.events[]? | select(.type == "relay")' >"$RELAY_TMP" 2>/dev/null; then
    log "jq failed to parse events batch; retrying"
    rm -f "$RELAY_TMP" >/dev/null 2>&1 || true
    sleep "$POLL_SECONDS"
    continue
  fi

  while IFS= read -r RELAY_EVT; do
    [[ -z "$RELAY_EVT" ]] && continue
    RELAY_ID=$(printf '%s' "$RELAY_EVT" | jq -r '.id')
    if is_seen_relay "$RELAY_ID"; then
      continue
    fi
    mark_seen_relay "$RELAY_ID"

    INCOMING_INTENT=$(printf '%s' "$RELAY_EVT" | jq -r '.payload.message.intent // ""')
    INCOMING_EXPECT_REPLY=$(printf '%s' "$RELAY_EVT" | jq -r '.payload.message.expect_reply // true')
    if [[ "$INCOMING_EXPECT_REPLY" != "true" && "$INCOMING_INTENT" != "DONE" ]]; then
      # No reply expected. Record as seen and move on.
      continue
    fi
    runner_set_status "active" "relay_seen" "" "" "relay_seen" "$INCOMING_INTENT"
    PROMPT=$(room_prompt "$ROLE" "$ROOM_JSON" "$RELAY_EVT" "$STARTED_MESSAGE_SENT")
    runner_set_status "active" "reply_generating" "" "" "reply_generating" "relay"
    OUTGOING=$(ask_openclaw "$PROMPT")
    OUT_INTENT=$(printf '%s' "$OUTGOING" | jq -r '.intent // "ANSWER"')
    runner_set_status "active" "reply_ready" "" "" "reply_ready" "$OUT_INTENT"
    send_message "$OUTGOING" "relay" "$RELAY_ID"

    ROOM_CHECK=$(json_request "GET" "$BASE_URL/rooms/$ROOM_ID" "$API_TOKEN" "")
    ROOM_STATUS_AFTER=$(printf '%s' "$ROOM_CHECK" | jq -r '.room.status // "active"')
    if [[ "$ROOM_STATUS_AFTER" != "active" ]]; then
      STOP_REASON_AFTER=$(printf '%s' "$ROOM_CHECK" | jq -r '.room.stop_reason // ""')
      runner_release "exited" "room_closed:${STOP_REASON_AFTER}"
      log "room ended after send status=$ROOM_STATUS_AFTER reason=$STOP_REASON_AFTER"
      break 2
    fi
  done <"$RELAY_TMP"
  rm -f "$RELAY_TMP" >/dev/null 2>&1 || true

  CURSOR="$BATCH_NEXT_CURSOR"
  if [[ "$STARTED_MESSAGE_SENT" == "1" ]]; then
    runner_set_status "idle" "poll_idle" "" "" "event_polling" "poll_idle"
  fi
  sleep "$POLL_SECONDS"
done
