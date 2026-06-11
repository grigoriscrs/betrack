#!/usr/bin/env bash
# Build frontend, open SSH SOCKS tunnel to the UK VPS for Betfair routing,
# then run serve.py. Cleans up the tunnel on Ctrl-C.
#
# Env overrides:
#   BETRACK_VPS_HOST     default: 84.8.153.111
#   BETRACK_VPS_USER     default: opc
#   BETRACK_VPS_KEY      default: ~/.ssh/betrack-vps.key
#   BETRACK_SOCKS_PORT   default: 1080
#   SKIP_BUILD=1         skip the npm build step
#   SKIP_PROXY=1         skip the SSH tunnel (Betfair will 403; Stoix/Novi still flow)

set -euo pipefail

cd "$(dirname "$0")"

VPS_HOST="${BETRACK_VPS_HOST:-84.8.153.111}"
VPS_USER="${BETRACK_VPS_USER:-opc}"
VPS_KEY="${BETRACK_VPS_KEY:-$HOME/.ssh/betrack-vps.key}"
SOCKS_PORT="${BETRACK_SOCKS_PORT:-1080}"

# --- frontend build ---
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    pushd betrack/web/frontend >/dev/null
    if [[ ! -d node_modules ]]; then
        echo "[run.sh] installing npm deps"
        npm install
    fi
    echo "[run.sh] building frontend"
    npm run build
    popd >/dev/null
fi

# --- SSH SOCKS tunnel ---
TUNNEL_PID=""

is_port_listening() {
    # Works on Linux/macOS/Git-Bash; falls back to a TCP probe.
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${SOCKS_PORT}$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -an 2>/dev/null | grep -E "[:.]${SOCKS_PORT}[[:space:]]+.*LISTEN" >/dev/null
    else
        (exec 3<>/dev/tcp/127.0.0.1/"$SOCKS_PORT") >/dev/null 2>&1
    fi
}

SSH_LOG="$(mktemp -t betrack-ssh.XXXXXX.log)"
cleanup() {
    if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        echo "[run.sh] closing SSH tunnel (pid $TUNNEL_PID)"
        kill "$TUNNEL_PID" 2>/dev/null || true
    fi
    rm -f "$SSH_LOG" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "${SKIP_PROXY:-0}" == "1" ]]; then
    echo "[run.sh] SKIP_PROXY=1 — running without Betfair routing"
elif is_port_listening; then
    echo "[run.sh] port $SOCKS_PORT already listening — reusing existing tunnel"
    export BETRACK_BETFAIR_PROXY="socks5h://127.0.0.1:${SOCKS_PORT}"
else
    if [[ ! -f "$VPS_KEY" ]]; then
        echo "[run.sh] SSH key not found at $VPS_KEY — set BETRACK_VPS_KEY or SKIP_PROXY=1" >&2
        exit 1
    fi
    echo "[run.sh] opening SSH SOCKS tunnel to ${VPS_USER}@${VPS_HOST} on :${SOCKS_PORT}"
    # -v gives us useful diagnostics in the log; ConnectTimeout caps the initial
    # TCP handshake; ServerAlive keeps long-lived tunnels healthy. Stderr → log
    # so we can show it on failure without spamming the normal run.
    ssh -i "$VPS_KEY" \
        -v \
        -D "$SOCKS_PORT" -N \
        -o ExitOnForwardFailure=yes \
        -o ConnectTimeout=15 \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new \
        "${VPS_USER}@${VPS_HOST}" 2>"$SSH_LOG" &
    TUNNEL_PID=$!

    # Wait up to ~20s for the SOCKS port to come up. Oracle Cloud first-hop
    # latency + StrictHostKeyChecking=accept-new on first connection can need
    # well over 5s. Bail early if the ssh process has already died.
    bound=0
    for _ in $(seq 1 40); do
        if is_port_listening; then bound=1; break; fi
        if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then break; fi
        sleep 0.5
    done

    if [[ "$bound" -ne 1 ]]; then
        echo "[run.sh] tunnel failed to bind :$SOCKS_PORT" >&2
        echo "[run.sh] last lines of ssh -v log ($SSH_LOG):" >&2
        tail -n 20 "$SSH_LOG" >&2 || true
        echo "" >&2
        echo "[run.sh] try: ssh -i \"$VPS_KEY\" -v ${VPS_USER}@${VPS_HOST}" >&2
        echo "[run.sh] or:  SKIP_PROXY=1 ./run.sh    (runs without Betfair routing)" >&2
        exit 1
    fi
    echo "[run.sh] tunnel up (pid $TUNNEL_PID)"
    export BETRACK_BETFAIR_PROXY="socks5h://127.0.0.1:${SOCKS_PORT}"
fi

# --- run the server ---
echo "[run.sh] starting serve.py"
python serve.py
