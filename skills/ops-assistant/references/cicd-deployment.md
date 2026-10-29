# 发布部署与 CI/CD

> 运维的核心矛盾：**业务要快，系统要稳**。发布策略就是在这两者之间找平衡。

## 发布策略对比

| 策略 | 原理 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| **滚动发布** | 逐批替换实例 | 简单、零额外资源 | 新旧版本共存，回滚慢 | 无状态服务、版本兼容 |
| **蓝绿发布** | 两套环境整体切换 | 秒级回滚（切回旧环境） | 双倍资源 | 关键服务、有状态变更 |
| **金丝雀/灰度** | 先放 5% 流量验证 | 爆炸半径最小 | 需要流量染色/按比例分流能力 | 大流量、高风险变更 |
| **停机发布** | 停了再启 | 最简单 | 有 downtime | 内部系统、可容忍停机 |

### 滚动发布要点（K8s）

```yaml
strategy:
  rollingUpdate:
    maxSurge: 1          # 最多多启动 1 个实例
    maxUnavailable: 0    # 全程可用实例数不减（滚动不抖动的关键）
minReadySeconds: 10      # 新 Pod 就绪后等 10 秒再放下一批
```

**滚动发布翻车三坑**：
1. 新旧版本不兼容（DB 结构变了旧代码不认）→ 变更必须**向前兼容**（先加字段上线，再删旧字段）
2. 没有 readinessProbe → 新 Pod 没起来就接流量
3. 没有优雅停机 → 滚动时用户请求被掐断（见下文）

### 优雅停机（发布不丢请求的前提）

```bash
# 应用收到 SIGTERM 后应该：
# 1. 停止接新请求（健康检查返回失败，从 LB 摘除）
# 2. 处理完手上请求（最长 30s）
# 3. 关闭连接池，退出

# K8s 侧配合：
spec:
  terminationGracePeriodSeconds: 30
  containers:
  - lifecycle:
      preStop:
        exec: { command: ["sh", "-c", "sleep 5"] }  # 等 LB/Endpoints 摘除生效
```

### 金丝雀发布（Nginx 按比例分流，无 K8s 也能做）

```nginx
# 95% 老版本，5% 新版本
upstream stable { server 10.0.1.10:8080; server 10.0.1.11:8080; }
upstream canary { server 10.0.2.10:8080; }

split_clients "${remote_addr}${request_id}" $backend {
    5%     canary;
    *      stable;
}

server {
    location / {
        proxy_pass http://$backend;
        # 金丝雀实例打标，日志里好区分
        proxy_set_header X-Canary $backend;
    }
}
```

放量节奏：1% → 5% → 20% → 50% → 100%，**每档观察至少 30 分钟**，盯错误率和 P99 延迟。

## 回滚设计（变更的另一半）

```bash
# 回滚三问，答不上来不许发：
# 1. 代码怎么回？（镜像/tag 保留上一个版本）
# 2. 数据怎么回？（DDL 变更了旧代码还能跑吗？）
# 3. 配置怎么回？（配置中心有版本历史吗？）
```

**数据库变更兼容原则**（回滚能力的根基）：
- ✅ 加字段（带默认值）、加索引、加表 —— 向前兼容
- ⚠️ 改字段类型 —— 需要双写过渡
- 🚫 删字段、改字段名 —— 必须分两期：先停写旧字段上线 → 观察 → 再删

```sql
-- 大表加索引（🚫 直接 ALTER 会锁表，千万级表能锁几小时）
-- MySQL 8.0：用 ALGORITHM=INPLACE, LOCK=NONE
ALTER TABLE orders ADD INDEX idx_user (user_id), ALGORITHM=INPLACE, LOCK=NONE;
-- 更稳妥：pt-online-schema-change 或 gh-ost（影子表方案）
```

## CI/CD 流水线

### GitLab CI 完整示例

```yaml
stages: [test, build, deploy-staging, deploy-prod]

variables:
  IMAGE: registry.example.com/myapp

test:
  stage: test
  script:
    - ./run-tests.sh              # 测试不过，后面全不跑
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

build:
  stage: build
  script:
    - docker build -t $IMAGE:$CI_COMMIT_SHORT_SHA .
    - docker push $IMAGE:$CI_COMMIT_SHORT_SHA
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

deploy-staging:
  stage: deploy-staging
  script:
    - helm upgrade --install myapp ./chart --set image.tag=$CI_COMMIT_SHORT_SHA -n staging
    - ./smoke-test.sh https://staging.example.com   # 冒烟测试
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

deploy-prod:
  stage: deploy-prod
  script:
    - helm upgrade --install myapp ./chart --set image.tag=$CI_COMMIT_SHORT_SHA -n prod
    - ./smoke-test.sh https://api.example.com || ./rollback.sh   # 冒烟失败自动回滚
  when: manual                    # 🚫 生产必须人工确认
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
```

### 流水线设计原则

1. **制品只构建一次**：测试环境用的镜像和生产是同一个（digest 一致），杜绝"我本地能跑"
2. **越往后越严格**：CI 全自动 → staging 自动 → prod 人工卡点
3. **密钥不进仓库**：用 CI 变量/Vault，`git grep` 定期扫泄露
4. **构建可重现**：锁定依赖版本（package-lock/go.mod/vendor）

### 无 CI 平台的最小可用发布脚本

```bash
#!/usr/bin/env bash
# deploy.sh —— 单/多机最小发布脚本（实测过语法）
set -euo pipefail

APP=myapp
VERSION=$1
SERVERS="10.0.1.10 10.0.1.11"
HEALTH="http://localhost:8080/healthz"

for s in $SERVERS; do
    echo "=== 发布 $s ==="
    ssh "$s" bash -s <<EOF
        set -e
        cd /opt/$APP
        cp -r current releases/$VERSION-\$(date +%s) 2>/dev/null || true  # 备份当前版本
        curl -sfO http://registry.internal/$APP-$VERSION.tar.gz
        tar xzf $APP-$VERSION.tar.gz -C releases/
        ln -sfn releases/$VERSION current      # 原子切换：软链替换
        systemctl reload $APP || systemctl restart $APP
EOF
    # 健康检查，不过则中止后续机器
    for i in $(seq 1 10); do
        ssh "$s" "curl -sf $HEALTH" && break
        [ $i -eq 10 ] && { echo "❌ $s 健康检查失败，已中止"; exit 1; }
        sleep 3
    done
    echo "✅ $s 完成"
done
```

**这个脚本的发布哲学**：软链切换（原子）+ 逐台发布（自带灰度）+ 健康检查卡点（自动中止）。

## 发布 checklist（贴墙上的那种）

```
发布前：
□ 低峰期？（别在流量高峰发）
□ 回滚方案演练过？
□ 相关方都通知了？（客服/上下游）
□ 监控大盘打开了？
发布中：
□ 第一批观察 30 分钟（错误率/延迟/业务指标）
□ 放量节奏记录
发布后：
□ 全量后观察 1 小时
□ 旧版本镜像保留（至少留到下次发布成功）
```
