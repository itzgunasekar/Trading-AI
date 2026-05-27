# =====================================================================
# D1 Portfolio Bot — per-user container
#
# Each approved user gets one of these containers running on the bot farm.
# Inside: Wine + MT5 terminal + Python bot.
#
# Build:
#   docker build -t d1bot:latest .
#
# Run (typically invoked by bot_farm/provision.py):
#   docker run -d --restart unless-stopped \
#     --name d1bot-USER_ID \
#     -e USER_ID=USER_ID \
#     -e CONTROL_PLANE_URL=https://api.d1portfolio.app \
#     -e BOT_TOKEN=<service-jwt> \
#     -v d1bot-USER_ID-data:/data \
#     d1bot:latest
# =====================================================================

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEDEBUG=-all
ENV WINEPREFIX=/root/.wine
ENV WINEARCH=win64
ENV DISPLAY=:99

# Wine + Python deps
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wine wine32 wine64 \
        xvfb \
        python3 python3-pip python3-venv \
        ca-certificates curl \
        cabextract gnupg \
        && rm -rf /var/lib/apt/lists/*

# Winetricks for installing VCRedist (needed by MT5)
RUN curl -L https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks \
        -o /usr/local/bin/winetricks && \
    chmod +x /usr/local/bin/winetricks

# Bootstrap Wine prefix (suppress dialogs)
RUN xvfb-run -a wineboot --init && \
    xvfb-run -a winetricks --unattended vcrun2019 || true

# Download MT5 (MetaQuotes installer URL).
# In production, host this artifact yourself.
# RUN curl -L https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe -o /tmp/mt5setup.exe && \
#     xvfb-run -a wine /tmp/mt5setup.exe /silent && \
#     rm /tmp/mt5setup.exe

WORKDIR /app

# Python bot dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Bot source (copied at build time)
COPY src/ /app/src/

# Bot data directory
RUN mkdir -p /data

# Entrypoint script does:
#   1. Fetch this user's config + decrypted MT5 password from control plane
#   2. Launch MT5 with those credentials (under xvfb-run wine)
#   3. Launch the Python bot (d1_portfolio_bot.py) attached to that MT5
#   4. Monitor and restart on crash
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
