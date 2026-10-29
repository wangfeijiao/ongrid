# 服务器与服务台账(部署时先核对)

## 阿里云服务器

| 项 | 值 | 备注 |
|---|---|---|
| APP 服务器 | 119.45.194.194 (Ubuntu) | 内网 10.10.0.5;系统用户 ubuntu;SSH 用户名/密钥方式:待确认(首次询问用户后回填) |
| DB 服务器 | 119.45.203.124 (PostgreSQL) | 内网 10.10.4.10,仅内网访问,不对外开端口 |
| Nginx SSL 证书 | `/etc/nginx/ssl/zyyjj.pem` + `zyyjj.key` | 泛域名证书,HTTPS 接口用 `curl -k` 验证 |

## 已知服务

| 域名/端口 | 项目 | /opt 代码路径 | systemd 服务 | 验证接口 |
|---|---|---|---|---|
| `https://api.zyyjj.cn:8443` | cnc-factory 等共用 API | 待确认 | 待确认 | `/api/health` 或 `openapi.json` |
| `https://dwg.zyyjj.cn:8444` | 图管家(zyyj-drawings) | `/opt/zyyj-drawings/backend` | `zyyj-drawings` | `GET /api/health` → `{"status":"ok"}` |
| 80 → `127.0.0.1:8000` | 量化选股(云) | `/opt/quant` | `quant` | `curl -I http://127.0.0.1:8000` |
| `192.168.1.185:8800` | 绩效系统(本地内网) | 本机目录,非 SSH | - | 见 zyyjj-perf-dev skill |

> 标"待确认"的项:首次部署该服务时向用户询问 SSH 用户、代码路径、systemd 服务名,确认后回填本表,后续直接复用。新增服务也按上表格式追加一行。
