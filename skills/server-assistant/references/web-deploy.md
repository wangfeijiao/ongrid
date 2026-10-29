# 部署网站：Nginx 站点配置、反向代理与 HTTPS

覆盖「装 nginx → 放静态站 → 配域名 → 反向代理 → 上 HTTPS → 访问不了怎么查」。

```bash
cat /etc/os-release    # ✅ 只读，看 NAME / VERSION_ID
nginx -v               # ✅ 只读，配置语法随版本变化
```

Ubuntu/Debian 用 `apt`；CentOS 7 用 `yum`，CentOS 8+/Rocky/AlmaLinux 用 `dnf`。CentOS 7 自带 nginx 版本很老，能升就升。

## 1. 安装 nginx 并放行端口

```bash
sudo apt update && sudo apt install nginx      # ⚠️ Ubuntu/Debian：有副作用，占用 80/443
sudo dnf install nginx                         # ⚠️ CentOS 8+/Rocky/Alma：有副作用
systemctl status nginx                         # ✅ 只读
```

CentOS 默认防火墙是 **firewalld**，配置都改完了还打不开，八成卡在这一层：

```bash
sudo firewall-cmd --list-all                         # ✅ 只读
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https    # ⚠️ 有副作用，--permanent 才写盘
sudo firewall-cmd --reload                           # ⚠️ 有副作用，短暂中断已有连接
```

Debian 系用 ufw：`sudo ufw allow 80/tcp && sudo ufw allow 443/tcp`。

为什么：云安全组是第一层、firewalld/ufw 是第二层、nginx 是第三层。为什么单独提 firewalld，就是因为它默认开启且默认不放 http。坑点：忘加 `--permanent` 只 reload，重启服务器后放行规则就没了；ufw 与 firewalld 同时开会互相打架，二选一。

## 2. 一个完整可复制的站点配置

存为 `/etc/nginx/sites-available/example.com`，同时支持静态页、SPA 前端路由和后端代理：

```nginx
server {
    listen 80;                  # IPv4 80
    listen [::]:80;             # IPv6，缺了会让 IPv6 客户端连不上
    server_name example.com www.example.com;

    root /var/www/example.com;  # URI 拼在它后面
    index index.html;           # 目录请求依次找的文件

    access_log /var/log/nginx/example.com.access.log;
    error_log  /var/log/nginx/example.com.error.log warn;

    # SPA 必须用 try_files，否则刷新 /about 直接 404
    location / {
        try_files $uri $uri/ /index.html;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

逐行要点：

- `listen [::]:80` 只写 IPv4 那条，IPv6 客户端会被拒绝；**第一个 server 块兜底**，别把正式站放成第一个。
- `root`：`/a.png` 读 `/var/www/example.com/a.png`；日志放 `/var/log/nginx/` 才能被 logrotate 轮转，放站点目录会直出。
- `try_files` 先找真实文件、再找目录、都找不到回 `/index.html` 交给前端路由。少了这个回退项，SPA 一刷新就 404；后端也接管 `/about` 时会和前端抢路由。

启用（Ubuntu/Debian 用 sites-available + sites-enabled）：`sudo ln -s /etc/nginx/sites-available/example.com /etc/nginx/sites-enabled/`（⚠️ 删链接=停用），然后 `nginx -t && systemctl reload nginx`（✅ 校验；⚠️ 平滑重载）。CentOS 系没有这套约定，直接写 `/etc/nginx/conf.d/example.conf`。

## 3. 静态站目录与权限

```bash
sudo mkdir -p /var/www/example.com                              # ⚠️ 有副作用
rsync -avz --delete ./dist/ deploy@服务器公网IP:/var/www/example.com/  # ⚠️ 覆盖同名文件
sudo find /var/www/example.com -type d -exec chmod 755 {} +    # ✅ 推荐
sudo find /var/www/example.com -type f -exec chmod 644 {} +    # ✅ 推荐
sudo chown -R www-data:www-data /var/www/example.com           # ⚠️ 仅需写权限时才做
```

为什么：nginx worker 以 `www-data` 运行，对目录要 **x** 才能进入、对文件要 **r** 才能读出。目录 640 或属主不对就是 403。

上传后 403 的常见原因（按概率）：

```bash
grep -R "root " /etc/nginx/sites-enabled/       # ✅ root 目录写错
ls -la /var/www/example.com                     # ✅ index 指定的文件不存在
namei -l /var/www/example.com/index.html        # ✅ 逐层看权限，找出缺 x 的那层
sudo setenforce 0 && curl -I http://127.0.0.1   # 🚫 仅验证 SELinux，别长期关闭
```

坑点：`chown -R www-data:www-data` 等于给该用户开了站点目录写权限，纯静态站保持 root 属主 + 755/644 更稳。

## 4. 反向代理：一条斜杠的差别

```nginx
location /api/ {
    # ❌ /api/foo 转发成 http://127.0.0.1:3000api/foo，必然 404
    # proxy_pass http://127.0.0.1:3000;
    # ✅ 有尾斜杠：/api/foo 转发成 http://127.0.0.1:3000/foo
    proxy_pass http://127.0.0.1:3000/;
}
```

为什么致命：匹配前缀 `/api/` 被原样拼到 upstream 后面，不写尾斜杠就成 `3000api/foo`。后端真要 `/api/foo` 这个路径时反而不能加斜杠——二选一，别混。

关键转发头：`Host $host`（真实域名）、`X-Real-IP $remote_addr`（真实客户端 IP）、`X-Forwarded-For $proxy_add_x_forwarded_for`（多层代理累加）、`X-Forwarded-Proto $scheme`（后端才知道是 https），`proxy_http_version 1.1` 是长连接前提。

WebSocket 再补两条：

```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;   # 客户端要升级协议
    proxy_set_header Connection "upgrade";    # 别写成分号分隔的 keep-alive
    proxy_read_timeout 3600s;                 # 默认 60s 会掐断长连接
}
```

坑点：`Upgrade` 写成大写或 `Connection` 写成 `upgrade, Connection` 都会握手失败；只配升级头但后端没开 ws，刷新页面同样异常。502 且 `error.log` 出现 `Connection refused while connecting to upstream`，说明上游没起或端口写错——别去改 nginx。

## 5. 校验与生效

```bash
nginx -t                     # ✅ 只读，检查语法与日志可写性
systemctl reload nginx       # ⚠️ 平滑重载：现有连接不断
systemctl restart nginx      # ⚠️ 有副作用，短暂中断
```

为什么用 reload：master 重读配置再平滑迁 worker，restart 会掐断全部连接，线上就是一次访问抖动。`nginx -t` 报权限类错误，通常是 `ssl_certificate` 之类路径属主不对或需 root 可读。

## 6. Let's Encrypt HTTPS

```bash
sudo apt install certbot python3-certbot-nginx   # ⚠️ Ubuntu 20.04+，插件名随 nginx 版本变化
sudo snap install --classic certbot && sudo ln -s /snap/bin/certbot /usr/bin/certbot  # ⚠️ Ubuntu 24.04 起推荐
sudo dnf install epel-release && sudo dnf install certbot nginx   # ⚠️ CentOS 系
sudo certbot --nginx -d example.com -d www.example.com --redirect # ⚠️ 会改写 nginx 配置
```

为什么 `--redirect`：certbot 会额外生成 80 强制 301 到 https 的 server 块。证书走 HTTP-01 挑战，所以**域名必须已解析到本机且 80 端口空闲**。

坑点：`Challenge failed / Timeout during connect` → 先 `dig +short example.com` 与 `curl -I http://example.com`，多半是解析没生效或安全组没开 80。Ubuntu 24.04 上 `python3-certbot-nginx` 装不上属正常，具体以当前官方文档为准。加了 `--redirect` 后访问 https 直接 502：80→443 跳转成功，但 443 那段 upstream 是死的，查 `systemctl status 你的服务`。证书 90 天过期，到期浏览器全红。

自动续期：`systemctl list-timers | grep certbot` 看 certbot.timer 在不在；crontab 里加：

```cron
0 3 * * * certbot renew --quiet --post-hook "systemctl reload nginx"
```

为什么必须带 `--post-hook`：renew 只换证书文件，nginx 不 reload 就还在用旧证书。renew 默认只在剩余 <30 天才动作，配成每天跑没问题；先 `sudo certbot renew --dry-run` 空跑验证。

## 7. "访问不了"排查清单（逐层验证）

```bash
dig +short example.com                    # ✅ 1 域名解析到哪
curl -I http://example.com                # ✅ 1 公网入口通不通
ss -tulnp | grep -E ':80|:443'            # ✅ 2 nginx 有没有监听
curl -I http://127.0.0.1                  # ✅ 2 绕过公网只测 nginx
systemctl status nginx && nginx -t        # ✅ 3 服务状态与语法
tail -50 /var/log/nginx/example.com.error.log  # ✅ 3 实际报错
systemctl status your-app                 # ✅ 4 后端在不在
curl -I http://127.0.0.1:3000             # ✅ 4 本机直连后端
sudo firewall-cmd --list-all              # ✅ 5 CentOS 防火墙
sudo ufw status                           # ✅ 5 Ubuntu 防火墙
```

判定法：`curl 127.0.0.1` 能通但公网不通 → 安全组/firewalld；nginx active 但仍 404 → `root` 或 `try_files`；502 → 上游没起或 `proxy_pass` 端口写错。

## 下一步

→ @references/runtime-env.md
→ @references/service-management.md
→ @references/server-security.md
