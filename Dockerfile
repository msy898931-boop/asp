# Dockerfile — ASTRA "always-on" server (backend host + Telegram bot).
#
# Two stages: build the C++ backend, then ship a slim runtime that starts the
# backend host — with the Telegram bot as its supervised sidecar — and serves
# the phone app (PWA) on the public port, reverse-proxying to the loopback API.
# The desktop UI is intentionally NOT part of this image.
#
# Build:
#   docker build -t astra-server .
# Run (local, both phone app and bot on :8790):
#   docker run -d --restart unless-stopped --env-file .env -p 8790:8080 astra-server

# ───────────────────────────── build stage ──────────────────────────────────
FROM debian:bookworm-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential cmake \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY CMakeLists.txt ./
COPY src ./src
COPY tests ./tests
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build --target aura_backend_host -j"$(nproc)"

# ───────────────────────── python deps stage ────────────────────────────────
# Web Push is the one runtime feature that needs a third-party package: the
# ECDH/AES-GCM crypto behind it is not in the standard library. Installing it in
# its own stage keeps the compiler out of the runtime image. It stays optional:
# without the VAPID keys, or without this directory, the service starts and
# answers "not configured" exactly as before.
FROM debian:bookworm-slim AS pydeps

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN printf 'pywebpush==2.5.0\n' > /tmp/webpush-requirements.txt \
 && python3 -m pip install --no-cache-dir --target /pylibs \
      -r /tmp/webpush-requirements.txt \
 && rm -f /tmp/webpush-requirements.txt

# ──────────────────────────── runtime stage ─────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend host binary.
COPY --from=build /src/build/aura_backend_host /app/aura_backend_host

# Python bridge + Telegram bot, in the layout PathResolver expects:
#   <appRoot>/resources/bridge/{mt5_python,telegram_bot}
COPY bridge/mt5_python  /app/resources/bridge/mt5_python
COPY bridge/telegram_bot /app/resources/bridge/telegram_bot

# Committed canonical XAUUSD M1 corpus, served as a synthetic data source so the
# pipeline runs without a Windows terminal. It is NOT a live broker feed.
COPY research/data/xauusd_m1/xauusd_m1_real.csv \
     /app/research/data/xauusd_m1/xauusd_m1_real.csv

# Default this deployment to the live source (overridable). It serves real gold
# prices from gold-api.com (spot) + Kraken (PAXGUSD candles) over the network,
# with no key and no Windows terminal. Set ASTRA_DATA_SOURCE=replay for an
# offline synthetic demo, or =mt5 on a Windows host with a live terminal.
ENV ASTRA_DATA_SOURCE=live \
    ASTRA_LIVE_CANDLE_PAIR=PAXGUSD \
    ASTRA_REPLAY_CSV=/app/research/data/xauusd_m1/xauusd_m1_real.csv \
    ASTRA_REPLAY_SYMBOL=XAUUSD

# Web Push crypto, from the pydeps stage. PYTHONPATH makes it importable by
# both processes that need it: the phone-app server and the bot. Without the
# VAPID keys the feature stays dormant, so this is a capability, not a default.
COPY --from=pydeps /pylibs /app/pylibs
ENV PYTHONPATH=/app/pylibs

# The phone app (PWA) is served by the same process, on the same public port,
# reverse-proxying /api/v1 and /bridge to the loopback backend. This makes the
# image deployable as ONE always-on service on a container host: the phone app
# and the Telegram bot share a single port.
COPY mobile  /app/mobile
# The signal tracker replays the closed bars the bridge already serves and is
# reverse-proxied at /tracker by the phone app. It has its own tiny HTTP server,
# so the entrypoint starts it as a loopback sidecar (see docker_entrypoint.sh).
COPY tracker /app/tracker
COPY advisor /app/advisor
# The akoan news-vs-results engine backs the phone's News tab (/akoan.json) and
# the weekly Telegram digest. Without it the route answers "engine unavailable".
COPY akoan /app/akoan
COPY scripts/docker_entrypoint.sh /app/docker_entrypoint.sh
RUN chmod +x /app/docker_entrypoint.sh

# Expose the system interpreter as a "bundled" runtime so BundleLocator finds
# it without the development-only --dev-system-python flag.
RUN mkdir -p /app/resources/python /app/config /app/data /app/logs \
 && ln -sf /usr/bin/python3 /app/resources/python/python3

# Default the public port to 8080 (the backend keeps 8790 on loopback, so the
# two never collide). Container hosts set $PORT themselves.
ENV PORT=8080 \
    API_PORT=8790 \
    BRIDGE_PORT=8791

# $PORT = phone app + reverse proxy; the loopback API/bridge stay private.
EXPOSE 8080

# tini reaps the bridge + bot children the host spawns. The entrypoint starts
# the backend host, then serves the phone app on $PORT.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker_entrypoint.sh"]

