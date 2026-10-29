# 常用命令模板(按需替换 <占位符>)

## 一、代码上传(本机执行)

```bash
# 改文件较多:rsync 整目录覆盖(注意 --delete 会删服务器多余文件,慎用)
rsync -avz --delete ./backend/ ubuntu@119.45.194.194:/opt/<project>/backend/

# 只改单个文件:scp
scp main.py ubuntu@119.45.194.194:/opt/<project>/

# 服务器已有 git 仓库时,直接在服务器上更新
ssh ubuntu@119.45.194.194
cd /opt/<project> && git pull
```

## 二、服务端操作(服务器上执行)

```bash
# 重启 + 查状态(必须确认 active (running))
sudo systemctl restart <service>
systemctl status <service> --no-pager

# 端口占用排查(重启失败的元凶往往是旧进程没死)
sudo ss -tlnp | grep 8443
sudo lsof -i :8443
sudo kill -9 <PID>          # 杀掉旧进程后再重启服务

# 日志
journalctl -u <service> -n 100 --no-pager      # systemd 日志
tail -f /opt/<project>/logs/<app>.log          # 应用日志

# 装依赖(仅新增依赖时)
cd /opt/<project> && venv/bin/pip install -r requirements.txt
```

## 三、部署验证(本机执行)

```bash
# HTTPS 服务(证书是泛域名,用 -k 跳过域名校验)
curl -k https://api.zyyjj.cn:8443/api/health
curl -k https://dwg.zyyjj.cn:8444/api/health      # 期望 {"status":"ok"}

# 验证本次改动:打对应业务接口(GET 列表 / POST 提交各来一发)
curl -k -X POST https://api.zyyjj.cn:8443/api/xxx \
  -H "Content-Type: application/json" -d '{"key":"value"}'
```

## 四、systemd 服务模板(/etc/systemd/system/<service>.service)

```ini
[Unit]
Description=<Description>
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/<project>/backend
EnvironmentFile=/opt/<project>/backend/.env        # 数据库连接串/密钥放这里
ExecStart=/opt/<project>/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8443
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now <service>
```

> 若应用无 .env 机制、把配置写在代码/常量里,可去掉 EnvironmentFile。

## 五、Nginx HTTPS 反代模板(/etc/nginx/conf.d/<project>.conf)

```nginx
server {
    listen 8443 ssl;
    server_name api.zyyjj.cn;
    ssl_certificate     /etc/nginx/ssl/zyyjj.pem;
    ssl_certificate_key /etc/nginx/ssl/zyyjj.key;
    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo nginx -t && sudo systemctl restart nginx
```

## 六、无 systemd 时的裸启动(临时/内网环境)

```bash
# 服务器上:后台跑 uvicorn,日志落文件
cd /opt/<project> && nohup venv/bin/uvicorn main:app --host 0.0.0.0 --port 8800 > app.log 2>&1 &

# 本机 Windows 等价操作(绩效系统 8800 就是这种):查端口 → taskkill → 重启
netstat -ano | grep 8800 | grep LISTEN
taskkill /F /PID <PID>
```
