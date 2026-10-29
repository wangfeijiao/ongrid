# 服务器文件传输：scp / rsync / sftp / 云控制台

解决「本地和服务器之间搬东西」的全部场景：单文件、整目录、增量同步、断点续传、中文文件名、传一半断了怎么办。约定：`本地` 表示在你自己电脑（macOS/Linux）执行；`服务器` 表示 ssh 登录后执行。SSH 端口以 22 为例。

## 一、scp 传单个文件

```bash
✅ scp /Users/me/site/index.html root@203.0.113.10:/var/www/html/index.html
✅ scp root@203.0.113.10:/var/log/nginx/access.log /Users/me/logs/access.log
```

**为什么**：scp 走已打通的 SSH 加密通道，服务器上不用额外开服务，最快能用。

**坑点 1（必踩）：端口参数是大写 `-P`**

```bash
✅ scp -P 2222 /Users/me/a.txt root@203.0.113.10:/tmp/a.txt    # 大写 P 才是端口
scp -p 2222 /Users/me/a.txt root@203.0.113.10:/tmp/a.txt       # ❌ 小写 -p 不是端口，是保留文件属性
✅ scp -P 2222 -p /Users/me/a.txt root@203.0.113.10:/tmp/a.txt # 端口 + 保属性一起用
```

**坑点 2：目录必须加 `-r`** —— 少了报 `not a regular file`（新手第一大报错）；**坑点 3：相对路径基准** —— `./a.txt` 是**当前 shell 所在目录**，不是「文档」也不是「桌面」，传错位置最常见的原因是开了新终端忘了 `cd`。

## 二、scp 的加密传输（macOS 与 Linux 差异大）

新版 `scp`（OpenSSH 8.0+）走 SFTP 子系统，参数部分不加密；要强制加密：

```bash
✅ scp -O /Users/me/a.txt root@203.0.113.10:/tmp/a.txt   # macOS / 高版本 OpenSSH 强制老协议
✅ tar -czf - -C /Users/me/site . | openssl aes-256-cbc -salt -pbkdf2 -out site.tar.gz.enc
✅ ssh root@203.0.113.10 "cd /tmp && openssl aes-256-cbc -d -pbkdf2 -in site.tar.gz.enc | tar -xzf -"
```

**为什么**：tar+openssl 两端算法与迭代参数必须完全一致，任一端版本不同就失败。macOS 自带 LibreSSL，能力跟 Linux 的 OpenSSL 不完全一致，报错时先 `openssl version`。参数细节**以当前系统 `man openssl-enc` 为准**。

## 三、rsync：唯一值得掌握的同步工具

```bash
✅ apt install rsync      # Ubuntu/Debian
✅ yum install rsync      # CentOS 7
✅ dnf install rsync      # CentOS 8+/Rocky/AlmaLinux
```

| 参数 | 作用 |
|---|---|
| `-a` | 归档模式：递归 + 保留权限/时间/属主/符号链接（=`-rlptgoD`），`-v` 输出详细过程 |
| `-z` | 传输压缩，代码/文本省 60%~80% 带宽 |
| `-P` | 进度条 + **断点续传**（隐含 `--partial`，保留半截文件） |
| `-e "ssh -p 2222"` | 指定 SSH 与端口 |
| `--exclude` | 排除规则，可重复多次 |
| `--delete` | 让目标与源**完全一致**（见下方高危警告） |

```bash
✅ rsync -avz -e "ssh -p 22" ./dist/ root@203.0.113.10:/var/www/html/
✅ rsync -avzP -e "ssh -p 22" ./dist/ root@203.0.113.10:/var/www/html/   # 断点续传，断了重来也行
✅ rsync -avzP -e "ssh -p 22" --exclude 'node_modules/' --exclude '.git/' \
     ./dist/ root@203.0.113.10:/var/www/html/
```

**为什么结尾斜杠如此重要**：`./dist/` 同步的是「dist 里面的内容」；写成 `./dist`（无斜杠）变成把 dist 目录本身塞进目标，结果得到 `/var/www/html/dist/...`，这就是「目录结构丢失」的根源。

**实测确认（rsync 2.6.9）**：本机跑 `rsync -av /tmp/src /tmp/dst2`（源无尾斜杠），日志里输出的是 `src/` 与 `src/a.txt`——目标目录下确实多出一层的**目录名 `src`**。所以这不是理论风险，是每次都可能踩到的默认行为：源路径结尾**永远**带 `/`。

**坑点**：`--exclude` 规则相对于**同步根路径**。`'node_modules'` 排掉所有层级同名目录；`'/node_modules'` 只排根目录那一个。

### `--delete`：它到底删什么

```bash
🚫 rsync -avz --delete -e "ssh -p 22" ./dist/ root@203.0.113.10:/var/www/html/
```

**为什么危险**：语义是「让目标变成源的镜像」——**源里没有的文件，目标上就删掉**。若源写成 `/home/deploy/`，而目标里有数据库、配置文件、`/.well-known` 证书目录，`--delete` 会在同步完成后把它们全部抹掉，且不可撤销。

**安全用法（三件套）**：

```bash
✅ rsync -avnz --delete -e "ssh -p 22" ./dist/ root@203.0.113.10:/var/www/html/  # ① 演练：一个字节都不动
✅ rsync -avz --delete --exclude '.well-known/' --exclude 'config.php' \
     -e "ssh -p 22" ./dist/ root@203.0.113.10:/var/www/html/                    # ② 护住关键目录
⚠️ tar -czf /root/pre-sync-backup-$(date +%F).tar.gz /var/www/html              # ③ 先备份再删
```

反向同步（服务器到本地做备份）：

```bash
✅ rsync -avzP -e "ssh -p 22" root@203.0.113.10:/var/www/html/ ./backup/site/   # 站点目录
✅ rsync -avzP -e "ssh -p 22" root@203.0.113.10:/var/lib/mysql/ ./backup/mysql/ # 数据库目录
```

## 四、sftp 交互式传文件

```bash
✅ sftp root@203.0.113.10
```

提示符变成 `sftp>` 之后（进服务器目录用 `cd`，进本地目录用 `lcd`）：

```bash
ls                # 服务器当前目录
cd /var/www/html  # 进服务器目录
lls               # 本地当前目录（l = local）
put index.html    # 上传单文件
put -r ./dist     # 递归上传目录
get access.log    # 下载单文件
byes              # 退出（q 也行）
```

**为什么**：`put` 是把自己手里的放上去，`get` 是把服务器的东西取回来，方向极容易记反——跟 `l`（本地）配 `lcd`/`lls`，跟远程路径配 `cd`/`ls`。

**坑点**：目录必须 `-r`；`put -r ./dist /var/www/html/` 末尾写斜杠，否则目标下会多一层 `dist/`。

## 五、tar 打包传输（最稳、保留权限）

```bash
✅ tar -pczf site-20260926.tar.gz -C /Users/me/site .
✅ scp -P 22 site-20260926.tar.gz root@203.0.113.10:/tmp/
✅ ssh root@203.0.113.10 "cd /var/www/html && tar -xpf /tmp/site-20260926.tar.gz"
```

**为什么**：单文件传输避免上千个文件逐个建连接（scp 传 1000 个小文件要跑到分钟级），`-p` 保留原权限，Nginx/PHP 需要它。`-C /Users/me/site .` 的含义是「切到该目录再打包当前目录内容」，解压后直接得到 `html/` 里的内容，不会多一层 `site/`。解压漏 `-p` 会让 `.php` 没有执行位，表现为浏览器下载源码而不是解析。

## 六、云控制台上传与对象存储

**阿里云 OSS / 腾讯云 COS**：控制台「文件管理 → 上传文件」，支持拖拽整个文件夹，适合首次部署。菜单与字段随平台改版，**以当前控制台为准**。

```bash
✅ ./ossutil cp -r ./dist oss://my-bucket/site/            # 阿里云
✅ ./coscli cp -r ./dist cos://my-bucket-1250000000/site/   # 腾讯云（down 方向同理换参数）
```

**坑点**：`ossutil`/`coscli` 首次需 `./ossutil config` 写入 SecretKey，密钥只存本地配置；换机器要重配，且不要把配置文件提交到 Git。

## 七、六大常见坑与正解

**1. 上传后网站 403，或访问 .php 变成下载源码**

```bash
✅ ps -o user= -C nginx                                    # ① 先确认 Web 运行用户
⚠️ chown -R www-data:www-data /var/www/html                # ② Ubuntu/Debian
⚠️ chown -R nginx:nginx /var/www/html                      #    CentOS/RHEL 系常见
⚠️ find /var/www/html -type d -exec chmod 755 {} \; -o -type f -exec chmod 644 {} \;
```

**为什么 403**：Nginx 以 `www-data` 运行，文件属主是 `root` 且目录无 `o+rx` 就读不到。**先确认运行用户再 chown**，别照抄别人的命令。

**2. 中文文件名乱码**

```bash
✅ rsync -avz --iconv=UTF-8,UTF-8-mac -e "ssh -p 22" ./素材/ root@203.0.113.10:/data/素材/
✅ LC_ALL=C scp -P 22 ./素材/图片.jpg root@203.0.113.10:/data/素材/
```

**为什么**：macOS 文件名是 UTF-8，老服务器可能是 GBK。`LC_ALL=C` 让本地 locale 不插手；`--iconv` 显式转码。可用编码名随版本变化，**以 `man rsync` 的 `--iconv` 条目为准**；服务器 locale 缺失（`locale -a` 看不到 `zh_CN.gbk`）时转码也会失败，需先 `apt install locales`。

**3. 大文件传输中断**

```bash
✅ rsync -avzP -e "ssh -p 22" ./big.zip root@203.0.113.10:/data/big.zip
✅ scp -P 22 -o Compression=yes ./big.zip root@203.0.113.10:/data/big.zip
```

传完**必须校验**：本机 `md5 ./big.zip` 对服务器 `ssh root@203.0.113.10 "md5sum /data/big.zip"`。

**4. 磁盘写满留下 `.partial` 垃圾**

```bash
✅ df -h /tmp                                    # 先看还剩多少
⚠️ find /var/www/html -name "*.partial" -delete # 清 rsync 半截文件
⚠️ find /tmp -name "*.partial" -delete
```

**5. 目录结构丢失**（续）：根因只有两个——rsync 源/目标没同时带斜杠，scp 忘 `-r` 或目标末尾缺 `/`。固化为习惯：目录同步一律 `rsync -avz 源目录/ 目标目录/`（两侧都带斜杠），scp 目录一律 `-r` 且目标末尾带 `/`。

**6. 顺序反了：文件传了但服务还在用旧配置**：正确顺序是**先同步 → 再 `systemctl reload nginx` 或重启应用**。

## 下一步

- 传上去服务起不来、端口不通 → @references/service-management.md
- 网站能起但 403/502 访问异常 → @references/web-deploy.md
- 要写定时同步或定时备份 → @references/maintenance-tasks.md
- 备份策略与恢复演练 → @references/backup-restore.md
