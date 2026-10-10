# Dockerfile — ASTRA "always-on" server (backend host + Telegram bot).
#
# Two stages: build the C++ backend, then ship a slim runtime that runs the
# backend host with the Telegram bot as its supervised sidecar. The desktop UI
# is intentionally NOT part of this image — this is the headless service that
# keeps the bot answering and pushing alerts 24/7.
#
# Build:
#   docker build -t astra-server .
# Run:
#   docker run -d --restart unless-stopped --env-file .env -p 127.0.0.1:8790:8790 astra-server

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

# Expose the system interpreter as a "bundled" runtime so BundleLocator finds
# it without the development-only --dev-system-python flag.
RUN mkdir -p /app/resources/python /app/config /app/data /app/logs \
 && ln -sf /usr/bin/python3 /app/resources/python/python3

# 8790 = frontend-facing loopback API (the bot reads it).
EXPOSE 8790

# tini reaps the bridge + bot children the host spawns.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/aura_backend_host"]
CMD ["--api-port", "8790", "--interval-ms", "5000"]
