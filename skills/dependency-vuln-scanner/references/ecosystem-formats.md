# 各生态依赖格式、锁文件与修复命令对照

深度解读或向用户解释时，对照本表，确保生态识别、锁文件说明、修复命令准确。

## 1. 支持的生态与文件

| 生态 | 直接依赖文件 | 锁文件（含传递依赖） |
|------|-------------|---------------------|
| Python (PyPI) | requirements.txt | poetry.lock / Pipfile.lock |
| Node.js (npm) | package.json | package-lock.json |
| Go | go.mod | （无独立锁文件，传递依赖在 go.mod 的 require 块） |
| Java (Maven) | pom.xml | （无锁文件，用 `mvn dependency:tree` 看传递依赖） |
| Rust (crates.io) | Cargo.toml | Cargo.lock |
| Ruby (RubyGems) | Gemfile | Gemfile.lock |
| PHP (Packagist) | composer.json | composer.lock |

## 2. 锁文件的价值（传递依赖）

- 直接依赖清单（如 requirements.txt）只含「你直接引入的库」；
- 锁文件记录了「依赖的依赖」（传递依赖）的精确版本；
- **大量漏洞藏在传递依赖里**，所以锁文件能查出更深一层的漏洞。

## 3. 修复命令对照

| 生态 | 升级到指定版本 | 移除依赖 |
|------|---------------|----------|
| PyPI | `pip install 包名==版本` | `pip uninstall 包名` |
| npm | `npm install 包名@版本` | `npm uninstall 包名` |
| Go | `go get 包名@版本` | `go mod edit -droprequire 包名` |
| Maven | 改 pom.xml 中版本号 | 从 pom.xml 移除依赖 |
| crates.io | `cargo add 包名@版本` | `cargo remove 包名` |
| RubyGems | `gem '包名', '版本'` | 从 Gemfile 移除 gem |
| Packagist | `composer require 包名:版本` | `composer remove 包名` |

## 4. 恶意包的处理

- 恶意包（OSV.dev 的 MAL- 条目）的修复是**移除**，不是升级——升级到新版本可能仍是恶意包；
- 恶意包的严重度高于任何 CVE（供应链投毒，可能窃取密钥/植入后门）。

## 5. 向用户解释时的注意事项

- **别说「锁文件」术语**：很多用户不懂。按生态说具体文件名——Python 是「poetry.lock」，Node 是「package-lock.json」；
- **Maven/Go 无锁文件**：如实说明，给出替代方案（`mvn dependency:tree`、看 go.mod 的 require 块），不要假装有锁文件可查；
- **非标准文件名也能识别**：脚本按内容特征识别，`requirements-dev.txt`、`req.txt`、`deps.json` 这类命名都能正确识别。
