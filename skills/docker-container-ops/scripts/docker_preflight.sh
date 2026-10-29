#!/usr/bin/env bash
# docker_preflight.sh — dev-004 docker-container-ops 预检：CLI 存在 + daemon 可达 + 权限
# 用法: bash scripts/docker_preflight.sh [--json] [--help]
# 退出码: 0 通过 | 1 daemon 不可达（可启动后重试） | 2 工具缺失
# docker 命令完整参数以 `docker --help` 为准（help 即真相）。
set -u

JSON_MODE=0

usage() {
cat <<'USAGE'
docker_preflight.sh — docker-container-ops 预检（CLI + daemon + 权限）

用法:
  bash docker_preflight.sh [--json]

选项:
  --json    输出 JSON 信封（机器可读）
  --help    显示本帮助并退出

退出码: 0 通过 | 1 daemon 不可达（启动 Docker 后重试） | 2 工具缺失
docker 命令完整参数请运行 `docker --help`。
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON_MODE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) printf '未知参数: %s（--help 查看用法）\n' "$1" >&2; exit 2 ;;
  esac
done

json_str() { printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

DAEMON_HINTS=(
  "Docker Desktop: 启动 Docker Desktop 后重试（Windows/macOS）"
  'Linux: sudo systemctl start docker && sudo usermod -aG docker $USER（重新登录生效）'
  "自定义 DOCKER_HOST 时确认其指向正确的 socket/tcp 地址"
)

# ---- 检查 1: docker CLI --------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  if [ "$JSON_MODE" -eq 1 ]; then
    printf '{"ok":false,"code":"TOOL_MISSING","message":"未找到 docker CLI","hints":["安装 Docker Desktop: https://www.docker.com/products/docker-desktop/","Linux: curl -fsSL https://get.docker.com | sh"],"data":null}\n'
  else
    echo "[FAIL] 未找到 docker CLI。安装: https://www.docker.com/products/docker-desktop/"
  fi
  exit 2
fi
cli_version=$(docker --version 2>/dev/null | sed -E 's/Docker version ([^,]+),.*/\1/')

# ---- 检查 2: daemon 可达 -------------------------------------------------
server_version=$(docker info --format '{{.ServerVersion}}' 2>/dev/null)
daemon_ok=0
[ -n "$server_version" ] && daemon_ok=1

# ---- 检查 3: 权限（能列出容器）------------------------------------------
perm_ok=0
if [ "$daemon_ok" -eq 1 ] && docker ps -q >/dev/null 2>&1; then
  perm_ok=1
fi

# ---- 软检查: compose 插件 ------------------------------------------------
compose_version=""
if docker compose version >/dev/null 2>&1; then
  compose_version=$(docker compose version --short 2>/dev/null)
fi

rc=0; code="OK"; msg="preflight 通过"
if [ "$daemon_ok" -ne 1 ]; then
  rc=1; code="DAEMON_UNREACHABLE"; msg="docker daemon 不可达（CLI 已装但连不上）"
elif [ "$perm_ok" -ne 1 ]; then
  rc=1; code="PERMISSION_DENIED"; msg="docker daemon 可达但无权列容器（Linux 用户组问题）"
fi

if [ "$JSON_MODE" -eq 1 ]; then
  ok="false"; [ $rc -eq 0 ] && ok="true"
  if [ $rc -eq 1 ] && [ "$code" = "DAEMON_UNREACHABLE" ]; then
    hints="\"${DAEMON_HINTS[0]}\",\"${DAEMON_HINTS[1]}\",\"${DAEMON_HINTS[2]}\""
  elif [ "$code" = "PERMISSION_DENIED" ]; then
    hints="'sudo usermod -aG docker \$USER 后重新登录'"
  else
    hints=""
  fi
  printf '{"ok":%s,"code":"%s","message":"%s","hints":[%s],"data":{"cli_version":"%s","server_version":"%s","compose_version":"%s"}}\n' \
    "$ok" "$(json_str "$code")" "$(json_str "$msg")" "$hints" \
    "$(json_str "$cli_version")" "$(json_str "$server_version")" "$(json_str "$compose_version")"
else
  echo "docker cli:  $cli_version"
  if [ "$daemon_ok" -eq 1 ]; then echo "daemon:      OK (server $server_version)"
  else echo "daemon:      不可达"; fi
  [ "$daemon_ok" -eq 1 ] && { [ "$perm_ok" -eq 1 ] && echo "permission:  OK" || echo "permission:  FAIL（无法列容器）"; }
  [ -n "$compose_version" ] && echo "compose:     $compose_version" || echo "compose:     未安装（docker compose 子命令不可用）"
  echo "result:      $msg"
fi
exit $rc
