# location 匹配规则

> Nginx 的 80% 配置问题都是"我以为会匹配到那个 location"。这块必须一次搞透。

## ⚠️ 先说一个大坑：平台差异（实测发现，网上教程几乎都不讲）

**在 macOS 和 Windows 上编译的 Nginx，`~` 和 `~*` 行为完全相同——一律大小写不敏感！**

实测验证（nginx 1.27.4 + PCRE2 10.44，macOS 源码编译）：`location ~ ^/Zebra$` 会匹配 `/zebra`、`/ZEBRA`。这不是 bug——nginx 源码 `src/os/unix/ngx_darwin_config.h` 中定义了 `NGX_HAVE_CASELESS_FILESYSTEM 1`（Windows 同理），因为大小写不敏感文件系统（HFS+/APFS/NTFS）上 `/Image.PHP` 和 `/image.php` 指向同一文件，若正则区分大小写会被绕过（安全设计）。

**实际影响**：
1. 在 Mac/Win 上开发验证的 location 规则，部署到 Linux 后 `/Index.PHP` 不再命中——"本地能跑服务器不行"的经典来源之一
2. 需要区分大小写的场景（如 `/v1/` 和 `/V1/` 是不同路由），在 mac/win 上根本无法通过 location 实现
3. 排障时先确认 `nginx -V` 的构建平台，别拿 Linux 结论套 mac 测试环境

## 五种匹配方式与优先级（实测验证）

```nginx
location = /exact      { return 200 "= 精确"; }          # ① 精确（最高）
location ^~ /static/  { return 200 "^~ 前缀"; }          # ② 前缀（命中后跳过正则）
location ~ \.php$     { return 200 "~ 正则"; }           # ③ 正则（区分大小写）
location ~* \.(jpg|png)$ { return 200 "~* 正则忽略大小写"; }  # ④ 正则（不区分大小写）
location /            { return 200 "/ 普通前缀"; }        # ⑤ 通配（最低）
```

**完整优先级算法**：
1. 先查所有**前缀匹配**（=、^~、普通前缀），记住**最长**的那个
2. `=` 精确命中 → 立即结束，用它
3. 最长前缀是 `^~` → 不再查正则，用它
4. 否则按**配置文件中出现顺序**查正则（`~` 和 `~*`），**第一个**命中的生效（正则不比长短，比位置！）
5. 没有正则命中 → 用第 1 步记住的最长普通前缀
6. 全没命中 → 404（或 `location /` 兜底）

### 实测对照表（请求 URI → 命中的 location；Linux 构建）

| 请求 | 命中 | 原因 |
|---|---|---|
| `/exact` | `= /exact` | 精确即停 |
| `/static/a.jpg` | `^~ /static/` | 前缀命中，跳过正则（`.jpg` 正则没机会） |
| `/foo.php` | `~ \.php$` | 正则命中 |
| `/foo.PHP` | `/`（兜底） | `~ \.php$` 区分大小写不匹配（⚠️ macOS/Win 构建会命中，见上文平台差异） |
| `/logo.PNG` | `~* \.(jpg\|png)$` | 忽略大小写正则命中（Linux 上 `~` 版不命中） |
| `/api/users` | `/` | 只有 `/` 前缀匹配 |
| `/static` | `/` | ⚠️ `^~ /static/` 带斜杠，`/static`（无斜杠）不匹配！ |

## 常见踩坑

### 坑 1：前缀匹配不带斜杠的边界

```nginx
location /api { ... }        # /api、/api2、/apixxx 全匹配！
location /api/ { ... }       # /api/xxx 匹配，/api 不匹配
# 想只匹配 /api 本身：用 = /api
```

### 坑 2：正则里匹配目录

```nginx
location ~ ^/api/ { ... }    # 正则也可以做前缀用，但注意它优先于普通前缀
# 正则与普通前缀打架时：正则赢（除非 ^~）
```

### 坑 3：正则顺序陷阱

```nginx
location ~ \.(jpg)$  { return 200 "A"; }
location ~ \.(jpg|png)$ { return 200 "B"; }
# /a.jpg 永远命中 A——正则按出现顺序，先到先得，不比"精确度"
```

### 坑 4：location 嵌套规则

```nginx
location /api/ {
    location /api/v1 { }     # ✅ 前缀里嵌前缀可以
    # location ~ /x { }      # ✅ 前缀里嵌正则可以（但外层若是正则则不能嵌正则！）
    # location = /exact { }  # ⚠️ 嵌套内 = 是相对外层的完整 URI 匹配
}
# 正则 location 里不能再嵌正则 location（配置错误，nginx -t 会拒绝）
```

## root vs alias（第二个高频坑）

```nginx
# root：location 前缀 + URI 拼接
location /static/ {
    root /var/www;          # /static/a.jpg → /var/www/static/a.jpg
}
# alias：location 前缀被替换
location /static/ {
    alias /var/www/files/;  # /static/a.jpg → /var/www/files/a.jpg
}

# ⚠️ alias 的目录末尾斜杠必须与 location 的对应，漏了就是路径拼接灾难
# ⚠️ 正则 location 里必须用 alias + 捕获组：
location ~ ^/download/(.*)$ {
    alias /data/files/$1;
}
```

## try_files（SPA 与前端路由的标配）

```nginx
# Vue/React SPA：存在文件直接返回，否则回 index.html
location / {
    root /var/www/dist;
    try_files $uri $uri/ /index.html;
}

# 拆解：$uri（文件存在吗）→ $uri/（目录，找 index）→ /index.html（兜底）
```

## 验证工具（写完配置自己测）

```bash
# 方法 1：return 200 做探针（改配置验证匹配）
location ~ ^/test { return 200 "matched regex"; }
nginx -t && nginx -s reload
curl http://localhost/test/x

# 方法 2：日志里打印命中信息（调试利器）
log_format debugmatch '$uri -> $status';
location /api/ { access_log /var/log/nginx/match.log debugmatch; }

# 方法 3：看真实加载配置（include 路径搞错时用它）
nginx -T | grep -A5 "server_name"
```
