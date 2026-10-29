# 运行环境安装：Node / Python / Java / Docker / PM2

装错方式和装不上一样让人头大。本文给「推荐做法 + 为什么 + 坑点」，所有命令按 Ubuntu/Debian（`apt`）与 CentOS/RHEL 系（`yum`/`dnf`）标注。

## 我要装 X，推荐哪种方式

| 要装的东西 | 推荐方式 | 什么时候不该这么选 |
|---|---|---|
| Node.js | 用 `nvm`（多版本/新版本）或官方 Nodesource 源 | 只装一个版本且 apt 够新 → 直接用 apt |
| Python 3 | 用 venv；需要特定版本用 `pyenv` | 别动系统 `python3`，别用 `--break-system-packages` |
| Java | 优先 `apt install openjdk-17-jdk`，要特殊版本再下 tar 包 | 生产环境别用 `apt install default-jdk` 一类的模糊包名 |
| Docker | docker-ce 官方源（三步） | 生产别用 `apt install docker`（Ubuntu 旧源版本危险） |
| Node 守护 | `npm i -g pm2` + `pm2 startup` | 系统级长期服务建议直接用 systemd（见 service-management.md） |

## 1. Node.js

Ubuntu 自带源版本往往偏旧（如 20.04 的 nodejs 10/12），新框架直接报 `EngineUnsupported`，先 `apt-cache policy nodejs` 看看源里能给多少版本（✅ 只读）。三种取法：

```bash
# 方式 A：Nodesource 官方源（Ubuntu/Debian）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -   # ⚠️ 会改写 sources.list
sudo apt install -y nodejs                    # ⚠️ 有副作用
# CentOS 系把上两行换成：curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash
sudo dnf install -y nodejs   # ⚠️ 部分 CentOS 可直接用 dnf（以当前官方文档为准）

# 方式 B：nvm（推荐，多版本共存最省事）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash  # ⚠️ 有副作用
source ~/.nvm/nvm.sh && nvm install 20 && nvm use 20 && nvm alias default 20   # ⚠️ 写 shell 配置
```

为什么 nvm 要 `source`：nvm 是 shell 函数不是二进制，**装完必须重开终端或手动 source 才生效**，否则报 `nvm: command not found`。

坑点：nvm 装的 node 在 `~/.nvm/versions/node/<v>/bin`，cron 和其他用户默认找不到，脚本里用绝对路径最稳。`npm i -g` 报 `EACCES`：别用 `sudo npm i -g`（全局包装进 root 目录，普通用户反而用不了），改用 nvm 装，或 `npm config set prefix ~/.npm-global` 再把 `~/.npm-global/bin` 加进 PATH（改了没加 PATH 就会 `command not found`，检查 `echo $PATH`）。卸载 `rm -rf ~/.nvm` 只删主体，shell 配置里那几行要手动清；apt 装的用 `sudo apt purge nodejs`。

## 2. Python

```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv   # ⚠️ Ubuntu
sudo dnf install python3 python3-pip                                   # ⚠️ CentOS 8+ / Rocky / Alma
python3 -m venv /opt/myapp/venv && source /opt/myapp/venv/bin/activate  # ⚠️ 建虚拟环境
```

为什么用 venv：系统 `python3` 是 OS 依赖（apt/dnf 的很多工具要用它），在里面装包会污染整个系统，升级系统时可能崩。

交给用户时的两个必踩坑：

```bash
# 1) Ubuntu 上 python3-venv 缺失
sudo apt install python3-venv    # ✅ 报错 ensurepip 缺失就装它（Debian 12 可能是 python3.11-venv）

# 2) pip 装包报 externally-managed-environment
pip install requests
# error: externally-managed-environment
```

正确解法（按优先级）：
1. 用 venv：`source venv/bin/activate && pip install requests`（最干净，推荐）。
2. 用系统包管理器：`sudo apt install python3-requests`。
3. 最后才用 `pip install --break-system-packages requests`——⚠️ 有副作用：包会被标记为「外来文件」，将来 `apt upgrade` 可能冲突，只在容器或一次性环境用。

`python3 -m pip check` 查依赖冲突。pyenv 装指定版本 `curl https://pyenv.run | bash`（⚠️ 改写 shell 配置）后 `pyenv install 3.11.9 && pyenv local 3.11.9`。

## 3. Java

```bash
sudo apt install openjdk-17-jdk            # ⚠️ Ubuntu/Debian；包名数字随版本变，17/11/21 都有
sudo dnf install java-17-openjdk-devel     # ⚠️ CentOS 系，-devel 才带 javac
```

要特定版本或 JDK 厂商时用官方 tar 包（以当前官网为准）：

```bash
mkdir -p /usr/lib/jvm && cd /usr/lib/jvm                          # ⚠️ 有副作用
sudo tar -xzf /tmp/jdk-17.0.9_linux-x64_bin.tar.gz -C /usr/lib/jvm   # ⚠️
sudo update-alternatives --install /usr/bin/java java /usr/lib/jvm/jdk-17.0.9/bin/java 1   # ⚠️
sudo update-alternatives --config java     # ✅ 只读交互，切换版本
```

配置 JAVA_HOME（写到 `~/.bashrc` 或 `/etc/profile.d/java.sh` 并 `source` 使其生效）：

```bash
export JAVA_HOME=/usr/lib/jvm/jdk-17.0.9      # ⚠️ 有副作用，路径要与实际解压目录一致
export PATH=$JAVA_HOME/bin:$PATH
```

坑点：应用报 `JAVA_HOME is not defined` 时，`echo $JAVA_HOME` 为空说明没 source 过（SSH 非交互登录不读 .bashrc）；systemd 服务里必须写死 `Environment=JAVA_HOME=...`，因为服务不继承你的 shell 环境。卸载 APT 包：`sudo apt purge openjdk-17-jdk`，残留用 `sudo apt autoremove`。

## 4. Docker 安装（docker-ce 官方源三步）

不要直接用 `apt install docker.io`（旧源版本陈旧）。Ubuntu/Debian 官方三步（以当前 Docker 官方文档为准，版本号可能是 `docker:gpg` 路径变化）：

```bash
sudo apt update
sudo apt install ca-certificates curl gnupg                      # ⚠️ 第 1 步：装依赖
sudo install -m 0755 -d /etc/apt/keyrings                        # ⚠️ 第 2 步：建 keyring 目录
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null       # ⚠️ 第 2 步：写源
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin  # ⚠️ 第 3 步
```

CentOS/RHEL 系用 `/etc/yum.repos.d/docker.repo` + `sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin`。

验证与开机自启：

```bash
sudo systemctl enable --now docker        # ⚠️ 有副作用：开机自启 + 立即启动
sudo docker run hello-world               # ⚠️ 会拉镜像，网络不通就是这里报 timeout
docker run --rm alpine echo ok            # ✅ 只读式验证（镜像不存在才拉）
```

免 sudo（**有安全隐患，慎重**）：

```bash
sudo usermod -aG docker deploy    # ⚠️ 有副作用
newgrp docker                     # 或重新登录才生效
```

为什么有风险：加入 docker 组等于给用户**根权限**——容器可挂载宿主 `/`，一个 `docker run -v /:/host` 就能读写整个文件系统。别把 docker 组给需要 sudo 的普通开发用户，需要就用 rootless docker。

国内镜像加速（`/etc/docker/daemon.json`，改完 `sudo systemctl restart docker`）：

```json
{
  "registry-mirrors": ["https://xxxx.mirror.aliyuncs.com", "https://docker.m.daocloud.io"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" }
}
```

坑点：改 `daemon.json` 语法错了 docker 起不来，`sudo systemctl status docker` 会指出文件行号。卸载：`sudo apt purge docker-ce docker-ce-cli containerd.io` + `sudo rm -rf /var/lib/docker`。

## 5. PM2 进程守护

```bash
npm install -g pm2                       # ⚠️ 有副作用，全局安装
pm2 start ecosystem.config.js            # ⚠️ 启动应用（配置文件见下）
pm2 save && pm2 startup systemd          # ⚠️ 生成开机自启，输出里那行要复制执行
pm2 logs app-name                        # ✅ 只读，看日志
pm2 list && pm2 info app-name            # ✅ 只读
pm2 restart app-name                     # ⚠️ 重启某个进程
```

ecosystem 配置（`ecosystem.config.js`，与 package.json 同级）：

```javascript
module.exports = {
  apps: [{
    name: 'myapp',
    script: 'npm',
    args: 'start',
    cwd: '/opt/myapp',
    env: { NODE_ENV: 'production', PORT: 3000 },
    instances: 2,          // cluster 模式多核利用
    autorestart: true,
    max_memory_restart: '500M'   // 内存超 500M 自动重启，防 OOM 拖垮机器
  }]
};
```

为什么 `pm2 save && pm2 startup`：`pm2 startup` 只生成 systemd 单元不记录进程列表，`pm2 save` 才把当前列表写进 dump，**少了 save 重启后进程不会回来**；`pm2 startup systemd` 打印出的那行 sudo 命令必须执行，否则开机自启不生效。坑点：PM2 日志默认无限增长，用 `pm2 set pm2:logs 10` 限制；`pm2 kill` 后 systemd 与 pm2 会抢同一个端口。

## 6. 多版本共存与卸载干净

nvm/pyenv 多版本天然共存，卸载删主体后**还要手清 shell 配置**：`rm -rf ~/.nvm` / `rm -rf ~/.pyenv`，再删掉 `~/.bashrc`、`~/.zshrc` 里的 `nvm init` / `pyenv init` 那几行。

apt 侧：`sudo apt remove pkg`（⚠️ 保留配置，重装会沿用旧配置）vs `sudo apt purge pkg`（⚠️ 连配置一起删）。判断是否残留看 `dpkg -l | grep pkg`，状态 `rc` 表示包已删配置还在，清理用 `sudo apt autoremove --purge`。全局 npm 残留先用 `npm ls -g --depth=0` 列清楚，⚠️ 直接删目录可能断掉其他包的依赖。

## 下一步

→ @references/web-deploy.md
→ @references/service-management.md
→ @references/server-security.md
