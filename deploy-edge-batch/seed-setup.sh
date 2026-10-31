#!/usr/bin/env bash
# seed-setup.sh — 在种子机上准备二进制源并起内网 HTTP 分发服务。
# 只有种子机访问公网，其余机器走内网拉取，避免 38 台同时打公网出口。
#
# 用法（root）：bash seed-setup.sh

set -euo pipefail

VERSION=v0.19.0
ARCH=linux-amd64
SRC=/opt/ongrid-edge-src
PORT=8000
DEPS_TAG=edge-deps-layout2-obi0.12.1-o0.157.0-n1.8.2-pr0.8.4-my0.19.0-pg0.19.1-r1.86.0-m0.51.0
CNB=https://cnb.cool/ongridio/ongrid-edge/-/releases/download
COMPONENTS="node_exporter process_exporter mysqld_exporter postgres_exporter redis_exporter mongodb_exporter obi obi.NOTICES otelcol-contrib"

log()  { printf '[edge-seed] %s\n' "$*"; }
fail() { printf '[edge-seed] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "must run as root"

mkdir -p "$SRC"
cd "$SRC"
command -v python3 >/dev/null || fail "python3 is required to serve binaries"

# ---------- 依赖包 ----------
if [[ ! -s "$SRC/TARGET" ]]; then
    log "fetching dependency archive from CNB"
    # 注意：CNB 的 .sha256 sidecar 记录的是原始文件名，必须同名下载才能 sha256sum -c
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$SRC/edge-deps-$ARCH.tar.xz" "$CNB/$DEPS_TAG/edge-deps-$ARCH.tar.xz"
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$SRC/edge-deps-$ARCH.tar.xz.sha256" "$CNB/$DEPS_TAG/edge-deps-$ARCH.tar.xz.sha256"
    (cd "$SRC" && sha256sum -c "edge-deps-$ARCH.tar.xz.sha256") || fail "deps checksum mismatch"
    log "extracting"
    tmp=$(mktemp -d)
    tar -xf "$SRC/edge-deps-$ARCH.tar.xz" -C "$tmp"
    for c in $COMPONENTS; do
        [[ -s "$tmp/$c" ]] || fail "component $c missing from archive"
        install -m 0755 "$tmp/$c" "$SRC/$c-$ARCH"
    done
    printf '%s\n' "$ARCH" > "$SRC/TARGET"
    rm -rf "$tmp" "$SRC/edge-deps-$ARCH.tar.xz" "$SRC/edge-deps-$ARCH.tar.xz.sha256"
    log "dependencies ready"
else
    log "dependencies already staged"
fi

# ---------- edge 主程序 ----------
if [[ ! -s "$SRC/ongrid-edge-$ARCH" ]]; then
    log "fetching ongrid-edge $VERSION"
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$SRC/ongrid-edge-$ARCH-$VERSION" "$CNB/$VERSION/ongrid-edge-$ARCH-$VERSION"
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$SRC/ongrid-edge-$ARCH-$VERSION.sha256" "$CNB/$VERSION/ongrid-edge-$ARCH-$VERSION.sha256"
    (cd "$SRC" && sha256sum -c "ongrid-edge-$ARCH-$VERSION.sha256") || fail "ongrid-edge checksum mismatch"
    mv "$SRC/ongrid-edge-$ARCH-$VERSION" "$SRC/ongrid-edge-$ARCH"
    chmod +x "$SRC/ongrid-edge-$ARCH"
    log "ongrid-edge ready"
else
    log "ongrid-edge already staged"
fi

# ---------- 起 HTTP 分发 ----------
pkill -f "http.server $PORT" 2>/dev/null || true
sleep 1
cd "$SRC"
nohup python3 -m http.server "$PORT" --bind 0.0.0.0 >/tmp/ongrid-seed-http.log 2>&1 &
disown 2>/dev/null || true
sleep 2
curl -fsS --connect-timeout 5 --max-time 10 "http://127.0.0.1:$PORT/TARGET" >/dev/null \
    || fail "http server failed to start; see /tmp/ongrid-seed-http.log"

log "serving $SRC on 0.0.0.0:$PORT"
ls -1 "$SRC"
