# GitLab CI 详细模式

本文件提供各语言和场景的 GitLab CI 精确配置模式。生成 `.gitlab-ci.yml` 时参考对应模式，确保语法和关键字正确。

---

## 通用结构

```yaml
stages:
  - lint
  - test
  - build
  - deploy

variables:
  # 全局变量
  DOCKER_TLS_CERTDIR: '/certs'

workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH == 'main'
    - if: $CI_COMMIT_BRANCH == 'develop'
    - if: $CI_COMMIT_TAG

default:
  interruptible: true
  # 同分支新提交自动取消旧 pipeline
```

- `workflow.rules`：控制整个 pipeline 的触发条件
- `interruptible`：允许新 pipeline 取消旧运行
- `variables` 中不要放敏感信息，用 CI/CD Variables（Settings → CI/CD → Variables）

---

## 缓存策略

```yaml
# 通用缓存模板
.node_cache: &node_cache
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
    policy: pull

.python_cache: &python_cache
  cache:
    key:
      files:
        - requirements.txt
    paths:
      - .pip-cache/
    policy: pull

.go_cache: &go_cache
  cache:
    key:
      files:
        - go.sum
    paths:
      - .go-cache/
```

缓存 `policy: pull` 用于后续 job（只读），首个安装 job 用 `policy: pull-push`。

---

## Node.js

### 基础模式

```yaml
.node_image: &node_image
  image: node:20-alpine

lint:
  <<: *node_image
  stage: lint
  cache:
    key:
      files: [package-lock.json]
    paths: [node_modules/]
    policy: pull-push
  script:
    - npm ci
    - npm run lint
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

test:
  <<: *node_image
  stage: test
  cache:
    key:
      files: [package-lock.json]
    paths: [node_modules/]
    policy: pull
  script:
    - npm ci
    - npm test
  coverage: '/All files[^|]*\|[^|]*\s+([\d.]+)/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

build:
  <<: *node_image
  stage: build
  cache:
    key:
      files: [package-lock.json]
    paths: [node_modules/]
    policy: pull
  script:
    - npm ci
    - npm run build
  artifacts:
    paths:
      - dist/
    expire_in: 1 week
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
    - if: $CI_COMMIT_TAG
```

### pnpm 模式

```yaml
.pnpm_setup: &pnpm_setup
  before_script:
    - corepack enable
    - corepack prepare pnpm@latest --activate
    - pnpm config set store-dir .pnpm-store
  cache:
    key:
      files: [pnpm-lock.yaml]
    paths:
      - .pnpm-store/

lint:
  image: node:20-alpine
  <<: *pnpm_setup
  stage: lint
  script:
    - pnpm install --frozen-lockfile
    - pnpm lint
```

---

## Python

### 基础模式

```yaml
.py_image: &py_image
  image: python:3.12-slim
  before_script:
    - pip install --cache-dir=.pip-cache -r requirements.txt

lint:
  <<: *py_image
  stage: lint
  cache:
    key:
      files: [requirements.txt]
    paths: [.pip-cache/]
    policy: pull-push
  script:
    - pip install ruff
    - ruff check .
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

test:
  <<: *py_image
  stage: test
  cache:
    key:
      files: [requirements.txt]
    paths: [.pip-cache/]
    policy: pull
  script:
    - pip install pytest pytest-cov
    - pytest --cov --cov-report=xml --cov-report=term
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH
```

### Poetry 项目

```yaml
.poetry_setup: &poetry_setup
  image: python:3.12-slim
  before_script:
    - pip install poetry
    - poetry config virtualenvs.in-project true
    - poetry install --no-interaction

test:
  <<: *poetry_setup
  stage: test
  cache:
    key:
      files: [poetry.lock]
    paths: [.venv/]
  script:
    - poetry run pytest --cov
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH
```

---

## Go

```yaml
.go_image: &go_image
  image: golang:1.22-alpine

lint:
  <<: *go_image
  stage: lint
  before_script:
    - go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
  script:
    - golangci-lint run ./...
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

test:
  <<: *go_image
  stage: test
  cache:
    key:
      files: [go.sum]
    paths: [.go-cache/]
  variables:
    GOCACHE: $CI_PROJECT_DIR/.go-cache
  script:
    - go test -race -coverprofile=coverage.out ./...
    - go tool cover -func=coverage.out
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

build:
  <<: *go_image
  stage: build
  script:
    - go build -o bin/app ./cmd/app
  artifacts:
    paths:
      - bin/app
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
    - if: $CI_COMMIT_TAG
```

---

## Java (Gradle)

```yaml
.gradle_setup: &gradle_setup
  image: eclipse-temurin:17-jdk
  before_script:
    - chmod +x gradlew
  cache:
    key:
      files: [gradle/gradle-wrapper.properties]
    paths:
      - .gradle/

test:
  <<: *gradle_setup
  stage: test
  script:
    - ./gradlew test
  artifacts:
    reports:
      junit: build/reports/tests/test/TEST-*.xml
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH

build:
  <<: *gradle_setup
  stage: build
  script:
    - ./gradlew build -x test
  artifacts:
    paths:
      - build/libs/*.jar
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
    - if: $CI_COMMIT_TAG
```

---

## Docker 构建 + 推送

### 使用 Kaniko（GitLab 推荐方式，无需 Docker daemon）

```yaml
build-image:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:debug
    entrypoint: ['']
  script:
    - /kaniko/executor
      --context "${CI_PROJECT_DIR}"
      --dockerfile "${CI_PROJECT_DIR}/Dockerfile"
      --destination "${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA}"
      --cache=true
      --cache-repo="${CI_REGISTRY_IMAGE}/cache"
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
    - if: $CI_COMMIT_TAG
```

### 推送到阿里云 ACR

```yaml
build-image:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  variables:
    ACR_REGISTRY: registry.cn-hangzhou.aliyuncs.com
  before_script:
    - docker login -u "${ACR_USERNAME}" -p "${ACR_PASSWORD}" "${ACR_REGISTRY}"
  script:
    - docker build --cache-from "${ACR_REGISTRY}/myorg/myapp:latest" -t "${ACR_REGISTRY}/myorg/myapp:${CI_COMMIT_SHORT_SHA}" .
    - docker push "${ACR_REGISTRY}/myorg/myapp:${CI_COMMIT_SHORT_SHA}"
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
```

### 多架构构建（ARM64 + AMD64）

```yaml
build-multiarch:
  stage: build
  image: docker:24
  services:
    - name: docker:24-dind
      command: ['--experimental']
  before_script:
    - docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
    - docker buildx create --use
    - docker login -u "${CI_REGISTRY_USER}" -p "${CI_REGISTRY_PASSWORD}" "${CI_REGISTRY}"
  script:
    - docker buildx build
      --platform linux/amd64,linux/arm64
      --push
      -t "${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA}"
      .
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
```

---

## K8s 部署

```yaml
deploy:
  stage: deploy
  image: bitnami/kubectl:latest
  needs: [build-image]
  environment:
    name: production
    url: https://myapp.example.com
  before_script:
    - echo "${KUBE_CONFIG}" | base64 -d > kubeconfig
    - export KUBECONFIG=kubeconfig
  script:
    - kubectl set image deployment/myapp myapp=${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA} -n production
    - kubectl rollout status deployment/myapp -n production --timeout=300s
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
      when: manual  # 生产部署需手动确认

deploy-staging:
  stage: deploy
  image: bitnami/kubectl:latest
  needs: [build-image]
  environment:
    name: staging
    url: https://staging.myapp.example.com
  before_script:
    - echo "${KUBE_CONFIG}" | base64 -d > kubeconfig
    - export KUBECONFIG=kubeconfig
  script:
    - kubectl set image deployment/myapp myapp=${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA} -n staging
    - kubectl rollout status deployment/myapp -n staging --timeout=300s
  rules:
    - if: $CI_COMMIT_BRANCH == 'develop'
```

---

## 安全扫描

### Trivy 容器扫描

```yaml
trivy-scan:
  stage: test
  needs: [build-image]
  image: aquasec/trivy:latest
  script:
    - trivy image --exit-code 1 --severity CRITICAL,HIGH "${CI_REGISTRY_IMAGE}:${CI_COMMIT_SHORT_SHA}"
  allow_failure: true
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
```

### SAST（GitLab 原生）

```yaml
include:
  - template: Security/SAST.gitlab-ci.yml
  - template: Security/Dependency-Scanning.gitlab-ci.yml
```

---

## 制品发布

### GitLab Package Registry

```yaml
publish:
  stage: deploy
  script:
    - npm publish --registry "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/npm/"
  rules:
    - if: $CI_COMMIT_TAG
```

---

## DAG 并行加速

使用 `needs` 关键字实现有向无环图（DAG），让无依赖关系的 job 并行执行：

```yaml
lint-frontend:
  stage: lint
  script: npm run lint
  rules: &mr_rule
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'

lint-backend:
  stage: lint
  script: golangci-lint run
  rules: *mr_rule

test-frontend:
  stage: test
  needs: [lint-frontend]
  script: npm test
  rules: *mr_rule

test-backend:
  stage: test
  needs: [lint-backend]
  script: go test ./...
  rules: *mr_rule

build-frontend:
  stage: build
  needs: [test-frontend]
  script: npm run build
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'

build-backend:
  stage: build
  needs: [test-backend]
  script: go build -o bin/app ./cmd/app
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
```

这样 lint-frontend 和 lint-backend 并行，各自的 test 等自己的 lint 完成后立即开始，不需要等另一个语言流。

---

## 多文件拆分

复杂项目可将 `.gitlab-ci.yml` 拆分为多个文件：

```yaml
# .gitlab-ci.yml（主文件）
include:
  - local: '.gitlab/ci/frontend.yml'
  - local: '.gitlab/ci/backend.yml'
  - local: '.gitlab/ci/deploy.yml'

stages:
  - lint
  - test
  - build
  - deploy

workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'
    - if: $CI_COMMIT_BRANCH
```

---

## 通知

### 飞书通知

```yaml
notify:
  stage: .post
  image: curlimages/curl:latest
  rules:
    - if: $CI_COMMIT_BRANCH == 'main'
      when: always
  script:
    - |
      STATUS="✅ 成功"
      if [ "$CI_JOB_STATUS" = "failed" ]; then STATUS="❌ 失败"; fi
      curl -X POST "${FEISHU_WEBHOOK}" \
        -H "Content-Type: application/json" \
        -d "{\"msg_type\":\"interactive\",\"card\":{\"header\":{\"title\":{\"tag\":\"plain_text\",\"content\":\"🚀 Pipeline ${STATUS}\"}},\"elements\":[{\"tag\":\"div\",\"text\":{\"tag\":\"lark_md\",\"content\":\"分支: ${CI_COMMIT_BRANCH}\\n提交: ${CI_COMMIT_SHORT_SHA}\"}}]}}"
```
