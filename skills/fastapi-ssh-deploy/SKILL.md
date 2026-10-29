---
name: fastapi-ssh-deploy
version: 1.0.0
display_name: FastAPI SSH 部署
display_name_en: FastAPI SSH Deploy
description_zh: 智裕云系列 FastAPI 后端 SSH 部署到 Linux(Ubuntu)的通用工作流。用于部署到阿里云 119.45.194.194(api.zyyjj.cn:8443 / dwg.zyyjj.cn:8444 等)、重启 systemd 服务、配置 Nginx 反代/SSL,或部署后验证接口。
description_en: Deploy and restart FastAPI backends (cnc-factory, 图管家, iot, 量化工具, etc.) on Ubuntu servers over SSH. Includes code upload, systemd restart, nginx reverse proxy/SSL and post-deploy verification. Applies to any FastAPI project deployed via systemd + uvicorn.
description: 智裕云系列 FastAPI 后端 SSH 部署到 Linux(Ubuntu)的通用工作流。当需要把改好的 FastAPI 代码部署到阿里云 119.45.194.194(api.zyyjj.cn:8443 / dwg.zyyjj.cn:8444 等)、重启 systemd 服务、配置 Nginx 反代/SSL、或部署后验证接口生效时使用。也适用于 iot、量化工具等任何 systemd+uvicorn 部署的 FastAPI 项目。This skill should be used when deploying or restarting FastAPI backends on Ubuntu servers over SSH, including code upload, systemd restart, nginx proxy and post-deploy verification.
agent_created: true
---

# FastAPI SSH 部署到 Linux(Ubuntu)

## 适用场景与服务器台账

部署前先读 `references/registry.md` 确认目标服务的 SSH 用户 / 代码路径 / systemd 服务名;标"待确认"的项首次部署时向用户询问补齐,然后回填 registry,之后直接复用。

- 阿里云 APP 服务器:119.45.194.194(Ubuntu,内网 10.10.0.5),代码在 `/opt/<project>`,systemd 管理
- 阿里云 DB 服务器:119.45.203.124(PostgreSQL,内网 10.10.4.10),仅内网可连,不对外
- 绩效系统:本地内网 192.168.1.185:8800,本机 uvicorn,非 SSH → 走 `zyyjj-perf-dev` skill
- 云上 Nginx SSL 证书统一放 `/etc/nginx/ssl/zyyjj.pem` + `zyyjj.key`

## 部署五步标准流程

1. **确认改动范围**:本次是前端 / 后端 / DB。先本机过语法:后端 `python -m py_compile main.py`(或 import 检查),前端脚本用 `node --check`。后端改动必须重启服务才生效;表结构改动依赖应用启动时 init_db 自动迁移。
2. **上传代码**:服务器已有 git 仓库 → ssh 进去 `git pull`;否则本地 `rsync -avz`(改文件多)或 `scp`(单文件)覆盖,命令见 `references/commands.md`。
3. **装依赖**(仅当新增了依赖时):`<project>/venv/bin/pip install -r requirements.txt` 或单装新包。
4. **重启服务**:`sudo systemctl restart <service>`;无 systemd 的服务用 nohup uvicorn 方式(模板见 commands.md)。重启失败先查端口占用,杀掉旧进程再启。
5. **验证**:先 `curl -k https://<域名>:<端口>/api/health` 等健康接口,再打本次改动的业务接口;小程序端让用户强刷或删缓存重进。收尾向用户汇报:改了前端还是后端、服务是否重启、验证结果。

## 关键坑(务必遵守)

- **uvicorn 不带 `--reload` 时改代码不生效**,必须重启服务。`systemctl restart` 后立刻 `status` 确认 active(running)。
- **旧进程占端口**会导致"新代码 404/405、但数据库却有新字段"的矛盾现象 → 先杀占用端口的旧进程再启动(命令见 commands.md)。
- **APP 与 DB 分离**:建表/迁移由应用启动时 init_db 自动执行,不要手工连 DB 服务器改;DB 只在内网 10.10.4.10。
- **新域名/端口上线**:阿里云安全组先放行,再在微信公众平台 → 开发管理 → 服务器域名,把域名加进 request 合法域名。
- **Nginx 改完先 `nginx -t` 再 `systemctl restart nginx`**;HTTPS 接口用 `curl -k` 验证(证书是泛域名 zyyjj.cn,需域名匹配)。
- 部署完成后给用户的变更说明 = 改动内容 + 根因 + 验证方法(强刷方式)。

## Bundled Resources

- `references/registry.md` — 服务器/服务台账(路径、服务名、验证接口,可回填)
- `references/commands.md` — 常用命令模板(systemd unit、nginx conf、上传、查端口、日志、验证)
