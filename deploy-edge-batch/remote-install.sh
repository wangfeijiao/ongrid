#!/usr/bin/env bash
# remote-install.sh — 在单台目标机上完成 ongrid-edge 部署。
#
# 主路径（主控已签发凭据）：
#   bash remote-install.sh <cloud_addr> <access_key> <secret_key> <seed_ip|->
# 例：
#   bash remote-install.sh 10.6.54.118:40012 <AK> <SK> 10.7.30.13
#
# 备用路径（Enrollment Token 自助注册，要求主控是 HTTPS 或 loopback）：
#   bash remote-install.sh - - - <seed_ip|-> <manager_url> <enrollment_token>
#
# 二进制缓存目录 /opt/ongrid-edge-bundle 保留，重跑可复用（幂等）。

set -euo pipefail

CLOUD_ADDR=${1:-}
ACCESS_KEY=${2:-}
SECRET_KEY=${3:-}
SEED_IP=${4:-}
MANAGER_URL=${5:-}
TOKEN=${6:-}

VERSION=v0.19.0
ARCH=linux-amd64
BUNDLE=/opt/ongrid-edge-bundle
DEPS_TAG=edge-deps-layout2-obi0.12.1-o0.157.0-n1.8.2-pr0.8.4-my0.19.0-pg0.19.1-r1.86.0-m0.51.0
CNB=https://cnb.cool/ongridio/ongrid-edge/-/releases/download
# install-edge.sh 按 <name>-<os>-<arch> 找这些文件
COMPONENTS="node_exporter process_exporter mysqld_exporter postgres_exporter redis_exporter mongodb_exporter obi obi.NOTICES otelcol-contrib"

log()  { printf '[edge-install] %s\n' "$*"; }
fail() { printf '[edge-install] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "must run as root (got uid $EUID)"

mkdir -p "$BUNDLE"
cd "$BUNDLE"

present() { [[ -s "$BUNDLE/$1" ]]; }

missing_components() {
    local miss=() c
    for c in $COMPONENTS; do present "$c-$ARCH" || miss+=("$c"); done
    printf '%s ' "${miss[@]:-}"
}

# ---------- 1) 二进制就位 ----------
pull_from_seed() {
    local name=$1
    curl -fsS --connect-timeout 8 --max-time 900 \
        -o "$BUNDLE/$name.part" "http://$SEED_IP:8000/$name" || return 1
    [[ -s "$BUNDLE/$name.part" ]] || { rm -f "$BUNDLE/$name.part"; return 1; }
    mv "$BUNDLE/$name.part" "$BUNDLE/$name"
    chmod +x "$BUNDLE/$name" 2>/dev/null || true
}

pull_from_cnb() {
    log "fallback: downloading dependency archive from CNB"
    local tmp; tmp=$(mktemp -d)
    # sidecar 记录的是原始文件名，必须同名下载才能校验
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$tmp/edge-deps-$ARCH.tar.xz" "$CNB/$DEPS_TAG/edge-deps-$ARCH.tar.xz"
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
        -o "$tmp/edge-deps-$ARCH.tar.xz.sha256" "$CNB/$DEPS_TAG/edge-deps-$ARCH.tar.xz.sha256"
    (cd "$tmp" && sha256sum -c "edge-deps-$ARCH.tar.xz.sha256") || fail "deps checksum mismatch"
    mkdir -p "$tmp/x" && tar -xf "$tmp/edge-deps-$ARCH.tar.xz" -C "$tmp/x"
    local c
    for c in $COMPONENTS; do
        [[ -s "$tmp/x/$c" ]] && install -m 0755 "$tmp/x/$c" "$BUNDLE/$c-$ARCH"
    done
    rm -rf "$tmp"
}

ensure_binaries() {
    local miss
    miss=$(missing_components)
    local need_edge=0
    present "ongrid-edge-$ARCH" || need_edge=1

    if [[ -z "${miss// /}" && $need_edge -eq 0 ]]; then
        log "binaries cached at $BUNDLE"
        return 0
    fi

    # 1a) 内网种子机（快，且不占公网出口）
    if [[ -n "$SEED_IP" && "$SEED_IP" != "-" ]]; then
        if curl -fsS --connect-timeout 8 --max-time 15 -o /dev/null "http://$SEED_IP:8000/TARGET"; then
            log "pulling binaries from seed $SEED_IP"
            local c ok=1
            for c in $COMPONENTS; do
                present "$c-$ARCH" || { pull_from_seed "$c-$ARCH" || ok=0; }
            done
            [[ $need_edge -eq 1 ]] && { pull_from_seed "ongrid-edge-$ARCH" || ok=0; }
            [[ $ok -eq 1 ]] && { log "seed pull complete"; return 0; }
            log "seed pull incomplete, falling back"
        else
            log "seed $SEED_IP not serving, falling back"
        fi
    fi

    # 1b) 官方 CNB
    [[ -z "${miss// /}" ]] || pull_from_cnb
    if ! present "ongrid-edge-$ARCH"; then
        log "downloading ongrid-edge $VERSION from CNB"
        curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
            -o "$BUNDLE/ongrid-edge-$ARCH-$VERSION" "$CNB/$VERSION/ongrid-edge-$ARCH-$VERSION"
        curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 20 \
            -o "$BUNDLE/ongrid-edge-$ARCH-$VERSION.sha256" "$CNB/$VERSION/ongrid-edge-$ARCH-$VERSION.sha256"
        (cd "$BUNDLE" && sha256sum -c "ongrid-edge-$ARCH-$VERSION.sha256") \
            || fail "ongrid-edge checksum mismatch"
        mv "$BUNDLE/ongrid-edge-$ARCH-$VERSION" "$BUNDLE/ongrid-edge-$ARCH"
        chmod +x "$BUNDLE/ongrid-edge-$ARCH"
    fi

    miss=$(missing_components)
    [[ -z "${miss// /}" ]] || fail "still missing components: $miss"
    present "ongrid-edge-$ARCH" || fail "ongrid-edge binary missing"
}

ensure_binaries

# ---------- 2) 凭据：优先主控已签发，其次 enroll 自助注册 ----------
if [[ -n "$ACCESS_KEY" && "$ACCESS_KEY" != "-" ]]; then
    [[ -n "$CLOUD_ADDR" && "$CLOUD_ADDR" != "-" ]] || fail "cloud_addr required with access_key"
    [[ -n "$SECRET_KEY" && "$SECRET_KEY" != "-" ]] || fail "secret_key required"
    export ONGRID_CLOUD_ADDR="$CLOUD_ADDR"
    export EDGE_ACCESS_KEY="$ACCESS_KEY"
    export EDGE_SECRET_KEY="$SECRET_KEY"
    log "using provisioned credentials (cloud=$CLOUD_ADDR)"
else
    [[ -n "$MANAGER_URL" && -n "$TOKEN" ]] || fail "either access_key or manager_url+token required"
    log "enrolling against $MANAGER_URL"
    rm -f "$BUNDLE/enroll.env"
    ONGRID_ENROLLMENT_TOKEN="$TOKEN" \
    "$BUNDLE/ongrid-edge-$ARCH" enroll \
        --manager-url "$MANAGER_URL" \
        --output "$BUNDLE/enroll.env" \
        --tls-insecure
    [[ -s "$BUNDLE/enroll.env" ]] || fail "enroll did not produce env file"
    set -a
    # shellcheck disable=SC1091
    . "$BUNDLE/enroll.env"
    set +a
    export ONGRID_CLOUD_ADDR="${ONGRID_EDGE_CLOUD_ADDR:?enroll env missing cloud addr}"
    export EDGE_ACCESS_KEY="${ONGRID_EDGE_ACCESS_KEY:?enroll env missing access key}"
    export EDGE_SECRET_KEY="${ONGRID_EDGE_SECRET_KEY:?enroll env missing secret key}"
    log "enrolled (cloud=$ONGRID_CLOUD_ADDR)"
fi

# ---------- 3) 安装 systemd 服务 ----------
log "running install-edge.sh"
bash "$BUNDLE/install-edge.sh" 2>&1 | tail -40

# ---------- 4) 结果自检 ----------
if systemctl is-active --quiet ongrid-edge; then
    log "RESULT=OK service active"
else
    log "RESULT=FAIL service not active"
    systemctl status ongrid-edge --no-pager || true
    exit 1
fi
