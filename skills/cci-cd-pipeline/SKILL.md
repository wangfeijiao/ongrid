---
name: "cci-cd-pipeline"
display_name: "CI/CD Pipeline 生成器"
display_name_en: "Cci Cd Pipeline"
description_zh: "自动生成 GitHub Actions / GitLab CI 流水线配置。当用户提到CI/CD流水线、GitHub Actions、GitLab CI、CI配置、CD流水线、持续集成、持续部署、pipeline配置、workflow配置、.gitlab-ci.yml、.github/workflows，或想了解\"帮我配CI/CD\"\"怎么写GitHub Actions\"\"GitLab CI怎么配\"\"项目流水线怎么搭\"时触发。不适用于Jenkins Pipeline、CircleCI、Travis CI等非GitHub/GitLab平台，也不适用于流水线的执行调试与运维。"
description_en: "AI-powered skill: Cci Cd Pipeline. GitHub Actions / GitLab CI CI/CD GitHub Actions GitLab CI CI CD pipeline workflow .gitlab-ci.yml .github/workflows \" CI/CD\"\" GitHub Actions\"\"GitLab CI \"\" \" Jenkins Pipeline CircleCI Travis CI GitHub/G..."
version: 1.0.0
agent_created: true
---

# CI/CD Pipeline 生成器

## 概述

为项目自动生成生产级 GitHub Actions 或 GitLab CI 流水线配置。不是生成一个只能跑 hello world 的骨架，而是产出包含构建、测试、lint、安全扫描、部署的完整 pipeline——开箱即用，符合工程最佳实践。

## 你的工作方式

```
1. 需求理解 → 识别项目技术栈、部署目标、平台偏好
2. 确认方案 → 向用户展示生成方案，获取确认
3. 生成配置 → 输出完整 pipeline 文件
4. 优化建议 → 补充可选增强项（缓存、矩阵构建、安全扫描等）
```

---

## Phase 1：需求理解

收到请求后，先收集生成 pipeline 所需的关键信息。优先从项目文件中自动推断，推断不了再问用户。

### 自动推断（读取项目文件）

按以下顺序检测项目根目录，从文件存在性推断技术栈：

| 文件 / 目录 | 推断结果 |
|-------------|---------|
| `package.json` | Node.js 项目，进一步读 `dependencies`/`scripts` 判断框架 |
| `requirements.txt` / `pyproject.toml` / `setup.py` | Python 项目 |
| `go.mod` | Go 项目 |
| `pom.xml` / `build.gradle` | Java 项目 |
| `Cargo.toml` | Rust 项目 |
| `Dockerfile` | 需要 Docker 构建 |
| `docker-compose.yml` | 有多服务编排 |
| `.github/` 目录存在 | 已有 GitHub Actions（询问是追加还是覆盖） |
| `.gitlab-ci.yml` 存在 | 已有 GitLab CI（询问是追加还是覆盖） |

同时读取以下文件获取更多上下文：
- `package.json` → `scripts` 字段（了解 build/test/lint 命令）
- `pyproject.toml` → `[tool.pytest]` / `[tool.ruff]` 配置
- `Makefile` → 已有的自动化命令
- `Dockerfile` → 构建步骤和基础镜像

### 需要确认的信息

自动推断后，向用户确认以下信息（推断出的直接列出，推断不了的提问）：

1. **CI 平台**：GitHub Actions 还是 GitLab CI？如果项目已有 `.github/` 或 `.gitlab-ci.yml`，以此推断
2. **部署目标**：Docker Registry / K8s / 云服务（AWS/GCP/阿里云）/ 静态托管 / 无需部署
3. **环境分支策略**：main 分支部署生产？dev 分支部署测试？用 tag 触发发布？
4. **额外需求**：是否需要安全扫描、代码覆盖率上传、通知（Slack/钉钉/飞书）、制品发布（GitHub Release / GitLab Package）

---

## Phase 2：确认方案

向用户展示生成方案摘要，包含：

- 平台选择
- Pipeline 阶段概览（如：lint → build → test → deploy）
- 每个阶段使用的关键工具/命令
- 触发条件（push/PR/tag/manual）
- 部署目标与环境

**示例方案摘要：**

> 为你的 Node.js + Next.js 项目生成 GitHub Actions 流水线：
> - **触发**：push 到 main/dev，所有 PR
> - **阶段**：lint (ESLint) → build (next build) → test (vitest) → deploy (Vercel)
> - **增强**：Node.js 版本矩阵 [18, 20]、pnpm 缓存、PR 评论覆盖率
> - **部署**：main → 生产环境，dev → 预览环境

用户确认后进入 Phase 3。如果用户要调整，修改方案后重新确认。

---

## Phase 3：生成配置

根据确认方案生成 pipeline 文件。生成时必须遵循以下原则。

### 通用原则

1. **生产级，不是教程级**：包含缓存、错误处理、超时设置、并发控制
2. **安全优先**：敏感信息用 secrets，不硬编码；checkout 用最小 fetch depth
3. **快速反馈**：lint 和快速测试放在前面，慢任务放后面；利用缓存减少重复下载
4. **可观测**：关键步骤有明确的输出和日志标识

### GitHub Actions 生成规范

读取 `references/github-actions-patterns.md` 获取各语言/场景的详细模式，遵循以下要点：

- 使用最新稳定版 actions（`actions/checkout@v4`、`actions/setup-node@v4` 等）
- 缓存依赖：`actions/cache` 或 setup-* 内置缓存
- 并发控制：同一分支的 workflow 自动取消旧运行
- 矩阵构建：需要多版本测试时使用 `strategy.matrix`
- PR 检查：用 `concurrency` 防止重复运行
- 部署用环境（environments）保护生产环境
- 输出文件路径：`.github/workflows/{name}.yml`

### GitLab CI 生成规范

读取 `references/gitlab-ci-patterns.md` 获取各语言/场景的详细模式，遵循以下要点：

- 使用 `stages` 明确定义阶段顺序
- 用 `rules` 而非 `only/except` 控制触发
- 使用 `cache` 缓存依赖，key 按分支隔离
- 用 `artifacts` 在 jobs 间传递构建产物
- 使用 `needs` 实现 DAG 并行加速
- 环境变量用 CI/CD Variables，生产环境加 `protected`
- 输出文件路径：`.gitlab-ci.yml`（单文件）或 `.gitlab/ci/` 目录（多文件拆分时）

### 文件组织

- **简单项目**：生成单个 workflow 文件即可
- **复杂项目**（多环境部署 + 多工作流）：按职责拆分文件
  - GitHub Actions：`.github/workflows/ci.yml` + `.github/workflows/deploy.yml`
  - GitLab CI：`.gitlab-ci.yml` 主文件 + `.gitlab/ci/` 下拆分子文件

---

## Phase 4：优化建议

配置文件生成后，列出可选增强项供用户选择。不要全部堆进生成结果——初始配置应该是开箱可用的，增强项按需加。

### 增强项菜单

| 增强项 | 说明 | 适合场景 |
|--------|------|---------|
| 安全扫描 | Snyk/Trivy/CodeQL | 生产项目、开源项目 |
| 代码覆盖率 | Coverage 上传 + PR 评论 | 有测试的项目 |
| 制品发布 | GitHub Release / GitLab Package | 库/CLI 项目 |
| 通知集成 | Slack/钉钉/飞书 webhook | 团队协作项目 |
| 矩阵构建 | 多版本/多 OS 并行测试 | 跨版本兼容的库 |
| Preview 部署 | PR 触发预览环境 | 前端项目 |
| 性能预算 | Lighthouse CI | Web 应用 |
| 依赖更新 | Dependabot/Renovate | 长期维护项目 |

用户选择后，追加对应配置到已生成的文件中。

---

## 工作流示例

### 示例 1：Node.js 项目的 GitHub Actions

用户说："帮我给这个 Node.js 项目配一下 GitHub Actions"

**推断**：读取 `package.json`，发现 Next.js 项目，有 `build`/`test`/`lint` scripts

**生成方案**：
```
触发: push main/dev, PR
阶段: lint → test → build → deploy
部署: Vercel (main→生产, dev→预览)
缓存: pnpm store
```

**输出**：`.github/workflows/ci.yml`

### 示例 2：Python 项目的 GitLab CI

用户说："这个 Python 项目用 GitLab CI，需要 Docker 构建推到阿里云 ACR"

**推断**：读取 `pyproject.toml`，发现 FastAPI 项目，有 `Dockerfile`

**生成方案**：
```
触发: push main/develop, tag v*
阶段: lint → test → build-image → deploy
部署: Docker 推送 ACR + K8s 部署
缓存: pip cache
```

**输出**：`.gitlab-ci.yml`

### 示例 3：多服务项目的完整 Pipeline

用户说："前后端分离项目，前端 Vite + 后端 Go，GitHub Actions 部署到 K8s"

**生成方案**：
```
触发: push main/develop, PR
阶段: 
  - lint (前端 ESLint + 后端 golangci-lint)
  - test (前端 vitest + 后端 go test)
  - build (前端 Docker build + 后端 Docker build)
  - deploy (helm upgrade)
缓存: pnpm + go module
```

**输出**：`.github/workflows/ci.yml` + `.github/workflows/deploy.yml`

---

## 资源目录

### references/

- `github-actions-patterns.md` — GitHub Actions 各语言/场景的详细模式（步骤模板、action 版本、缓存策略、部署模式）
- `gitlab-ci-patterns.md` — GitLab CI 各语言/场景的详细模式（job 模板、缓存配置、artifacts 传递、部署模式）

生成配置时读取对应参考文件获取精确模板，不要凭记忆拼凑 action 版本号或配置语法。

