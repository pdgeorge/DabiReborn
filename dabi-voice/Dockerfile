# ---- Stage 1: build Rhubarb Lip Sync ---------------------------------------
# No official ARM64 release binaries exist, so we compile from source.
# This stage is slow on a Pi (~15-30 min) but only runs on first build /
# version bump — Docker caches it afterwards.
FROM debian:bookworm-slim AS rhubarb

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git cmake g++ make \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v1.14.0 \
        https://github.com/DanielSWolf/rhubarb-lip-sync.git /src

WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target rhubarb -j"$(nproc)"

# The binary lands next to its res/ resource folder somewhere under build/ —
# locate it robustly and collect the whole directory.
RUN mkdir -p /opt/rhubarb \
    && BIN="$(find build -type f -name rhubarb | head -n1)" \
    && cp -r "$(dirname "$BIN")"/. /opt/rhubarb/ \
    && /opt/rhubarb/rhubarb --version

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rhubarb /opt/rhubarb /opt/rhubarb
ENV RHUBARB_BIN=/opt/rhubarb/rhubarb

COPY dabi-voice/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY dabi-voice/ .

EXPOSE 8090
CMD ["python", "app.py"]
