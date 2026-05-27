#!/bin/bash
# =====================================================================
# Per-user bot container entrypoint.
#
# Required environment variables:
#   USER_ID            — UUID of this user
#   CONTROL_PLANE_URL  — base URL of FastAPI control plane (https://...)
#   BOT_TOKEN          — service-issued JWT for this container
#                        (admin scope, expires in 24h, rotated by control plane)
#
# What this script does:
#   1. Fetches the user's bot config + decrypted MT5 password from the control
#      plane (over TLS, with BOT_TOKEN as bearer auth)
#   2. Writes a per-user config file the bot reads at startup
#   3. Launches Xvfb + Wine + MT5 + Python bot
#   4. Restarts on crash with exponential backoff
# =====================================================================
set -euo pipefail

: "${USER_ID:?USER_ID env var is required}"
: "${CONTROL_PLANE_URL:?CONTROL_PLANE_URL env var is required}"
: "${BOT_TOKEN:?BOT_TOKEN env var is required}"

echo ">>> D1 bot container starting for user=$USER_ID"

CONFIG_OUT=/data/user_config.json
mkdir -p /data

# --- 1. Fetch config from control plane (includes MT5 password) -----
echo ">>> fetching bot config..."
HTTP=$(curl -sw "%{http_code}" -o $CONFIG_OUT \
    -H "Authorization: Bearer $BOT_TOKEN" \
    "$CONTROL_PLANE_URL/internal/bot-config?user_id=$USER_ID")
if [ "$HTTP" != "200" ]; then
    echo "FATAL: could not fetch config (HTTP $HTTP)" >&2
    cat $CONFIG_OUT >&2 || true
    exit 1
fi

# --- 2. Start Xvfb for headless GUI (MT5 has a GUI even when used by API) ---
Xvfb :99 -screen 0 1024x768x16 &
XVFB_PID=$!
echo ">>> Xvfb started (PID $XVFB_PID)"

# --- 3. Launch MT5 with creds ---
MT5_ACCOUNT=$(jq -r '.mt5_account' $CONFIG_OUT)
MT5_SERVER=$(jq -r '.mt5_server' $CONFIG_OUT)
# MT5 password lives only in memory — never written to disk
MT5_PASSWORD=$(jq -r '.mt5_password' $CONFIG_OUT)

echo ">>> launching MT5 for account $MT5_ACCOUNT on $MT5_SERVER..."
# /portable forces config in current dir so multiple instances don't collide
DISPLAY=:99 wine "C:/Program Files/MetaTrader 5/terminal64.exe" \
    /portable \
    /login:$MT5_ACCOUNT \
    /server:$MT5_SERVER \
    /password:$MT5_PASSWORD &
MT5_PID=$!
unset MT5_PASSWORD   # wipe from env
sleep 30   # let MT5 connect and sync

# --- 4. Launch the Python bot with restart-on-crash loop ---
BACKOFF=5
while true; do
    echo ">>> launching d1_portfolio_bot for user=$USER_ID"
    if python3 /app/src/d1_portfolio_bot.py --user-config $CONFIG_OUT; then
        echo ">>> bot exited cleanly — shutting down"
        break
    fi
    echo "!!! bot crashed — restarting in ${BACKOFF}s"
    sleep $BACKOFF
    # Exponential backoff capped at 5 minutes
    BACKOFF=$(( BACKOFF * 2 ))
    [ $BACKOFF -gt 300 ] && BACKOFF=300
done

# Cleanup
kill $MT5_PID $XVFB_PID 2>/dev/null || true
echo ">>> container exiting"
