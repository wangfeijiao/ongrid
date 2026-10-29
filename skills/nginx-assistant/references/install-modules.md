# 安装、模块与升级

## 安装方式选型

| 方式 | 适合 | 特点 |
|---|---|---|
| **发行版包**（apt/yum） | 标准用途、快速起步 | 稳定但版本旧；模块固定 |
| **官方源码编译** | 需要第三方模块/定制 | 灵活；升级要重编 |
| **OpenResty** | 需要 Lua/网关能力 | 带 resty 全家桶 |
| **Docker（nginx 官方镜像）** | 容器化部署 | 隔离干净；配置外挂 |
| nginx plus（商业版） | 主动健康检查/动态 API | 花钱 |

## 源码编译完整流程（实测验证）

```bash
# 依赖：pcre2（rewrite）、zlib（gzip）、openssl（ssl）
# 三者都可以让 nginx 的 configure 直接带源码编译：

wget http://nginx.org/download/nginx-1.27.4.tar.gz
wget https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.44/pcre2-10.44.tar.gz

tar xzf nginx-1.27.4.tar.gz && cd nginx-1.27.4

./configure \
  --prefix=/opt/nginx \
  --with-pcre=../pcre2-10.44 \
  --with-http_ssl_module \          # HTTPS
  --with-http_v2_module \           # HTTP/2
  --with-http_stub_status_module \  # 状态页（监控必备）
  --with-http_realip_module \       # 真实 IP（LB 后必装）
  --with-stream \                   # TCP/UDP 四层代理
  --with-threads                    # 线程池（大文件 aio）
# ⚠️ 默认就有的：rewrite、proxy、gzip、access/log；SSL 需要显式 --with-http_ssl_module

make -j$(nproc) && make install

# macOS 注意：编译选项 --with-cc-opt="-Wno-deprecated-declarations" 抑制警告
```

**验证编译进了哪些模块**：

```bash
nginx -V    # 大写 V：显示 configure 参数（模块是编译时决定的，运行时看不了）
```

## 常用第三方模块

| 模块 | 用途 |
|---|---|
| ngx_cache_purge | proxy_cache 主动清除 |
| ngx_brotli | Brotli 压缩 |
| headers-more | 更灵活的响应头操作（more_set_headers） |
| ngx_upstream_check_module | 主动健康检查 |
| njs | JavaScript 动态逻辑 |

**动态模块（1.9.11+）**：`--add-dynamic-module=/path` 编出 .so，配置里 `load_module modules/ngx_http_foo_module.so;`——升级主程序不用重编第三方模块（版本 ABI 必须匹配！）。

## 平滑升级二进制（不断连接）

```bash
# 场景：换 nginx 版本，但不能断流量（reload 不会换二进制！）
# 原理：新二进制起 worker，老 master 优雅退役

1. 备份旧二进制：cp /usr/sbin/nginx /usr/sbin/nginx.old
2. 编译新版本（configure 参数保持一致：nginx -V 抄旧参数）
3. 替换二进制：cp objs/nginx /usr/sbin/nginx
4. 给老 master 发 USR2：kill -USR2 $(cat /var/run/nginx.pid)
   → 新 master + 新 worker 起来，老 worker 停止 accept
5. 给老 master 发 WINCH（优雅停老 worker）：kill -WINCH $(cat /var/run/nginx.pid.oldbin)
6. 验证无误后：kill -QUIT $(cat /var/run/nginx.pid.oldbin)   # 老 master 退出
# ⚠️ 中途发现问题：kill -HUP 老进程 ID 可回滚到旧版 worker
```

## Docker 部署要点

```dockerfile
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY conf.d/ /etc/nginx/conf.d/
# 静态资源（前端镜像常见）
COPY dist/ /usr/share/nginx/html/
```

```yaml
# docker-compose 挂载 + 日志进 stdout
services:
  nginx:
    image: nginx:1.27-alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    # 配置改后：docker exec nginx nginx -s reload（容器内 reload，不用重启容器）
    logging:
      driver: json-file
      options: { max-size: "50m", max-file: "3" }
```

## 版本选择（2026）

```
稳定分支 1.26.x（stable）—— 生产保守选择
主线分支 1.27.x（mainline）—— 新特性（QUIC/HTTP3 成熟）—— 通常也没问题
原则：
- 用包管理器的 LTS 源，或锁 nginx:1.26-alpine 这类精确 tag
- 🚫 别用 latest
- 大版本升级前读 CHANGES（配置指令废弃清单）
```

## 系统服务（systemd）

```ini
# /etc/systemd/system/nginx.service
[Unit]
Description=nginx
After=network.target

[Service]
Type=forking
PIDFile=/var/run/nginx.pid
ExecStartPre=/usr/sbin/nginx -t            # 启动前语法检查（防开机带病）
ExecStart=/usr/sbin/nginx
ExecReload=/usr/sbin/nginx -s reload
LimitNOFILE=100000

[Install]
WantedBy=multi-user.target
```
