---
name: 'cloud-ip-checker'
display_name: '云服务主机 IP 鉴别'
display_name_en: 'Cloud IP Checker'
description: '输入一个 IP 地址，判断它是否为云服务主机 IP（云厂商虚机/实例网段）。若是，输出具体的云服务归属单位（如「阿里云（Alibaba Cloud）」「腾讯云（Tencent Cloud）」「亚马逊云科技（AWS）」等）；若不是，输出「否」。支持 IPv4/IPv6、单 IP 与批量（stdin 每行一个）。适用于安全研判、资产归属识别、访问来源分析等场景。'
description_zh: '输入一个 IP 地址，判断它是否为云服务主机 IP（云厂商虚机/实例网段）。若是，输出具体的云服务归属单位（如「阿里云」「腾讯云」「AWS」等）；若不是，输出「否」。支持 IPv4/IPv6、单 IP 与批量查询，适用于安全研判、资产归属识别与访问来源分析。'
description_en: 'Input an IP address and determine whether it is a cloud service host IP (VM/instance network segment of a cloud provider). If yes, output the specific cloud service owner (e.g. Alibaba Cloud, Tencent Cloud, AWS); if not, output "No". Supports IPv4/IPv6, single IP and batch lookup (stdin, one per line). Useful for security analysis, asset attribution and traffic source identification.'
version: '1.0.0'
author: 'WorkBuddy'
level: 'personal'
metadata:
  category: 'network-security'
  tags: ['ip', 'whois', 'rdap', 'cloud', '归属查询', '安全研判']
---

# 云服务主机 IP 鉴别 Skill

## 功能

给定一个 IP，判定它是否属于某家公有云/云服务厂商的主机网段，并返回**归属单位**。

- 命中云服务厂商 → 输出 `归属单位（英文）`，例如：`阿里云（Alibaba Cloud）`、`腾讯云（Tencent Cloud）`、`华为云（Huawei Cloud）`、`亚马逊云科技（AWS）`、`微软云（Microsoft Azure）`、`谷歌云（Google Cloud）` 等。
- 未命中 → 输出 `否`。
- 命中运营商/ISP 骨干网（中国电信/联通/移动等）但无云主机专属特征时，也输出 `否`，并附说明（普通宽带/专线不是云主机）。

## 何时使用

用户说「查一下这个 IP 是不是云服务器」「这个 IP 是哪家的云」「判断 IP 归属」「是不是阿里云/腾讯云的机器」等，或任何需要识别 IP 背后云厂商归属的场景。

## 调用方式

脚本路径（按优先级取其一）：
- `${CODEBUDDY_SKILL_DIR}/scripts/check_cloud_ip.py`
- 相对路径 `scripts/check_cloud_ip.py`（在 skill 目录下执行）

### 1. 单 IP（推荐，末尾 `RESULT:` 行可直接读取结论）

```bash
python3 "${CODEBUDDY_SKILL_DIR}/scripts/check_cloud_ip.py" 47.119.176.109
```

### 2. 批量（从标准输入，每行一个 IP）

```bash
printf "47.119.176.109\n8.8.8.8\n119.29.29.29\n" | python3 "${CODEBUDDY_SKILL_DIR}/scripts/check_cloud_ip.py" -
# 或读取文件
cat ip_list.txt | python3 "${CODEBUDDY_SKILL_DIR}/scripts/check_cloud_ip.py" -
```

### 3. 机器可读（JSON）

```bash
python3 "${CODEBUDDY_SKILL_DIR}/scripts/check_cloud_ip.py" --json 119.29.29.29
```

## 输出解读

- 人类可读模式：每行输出 `IP  =>  是云服务主机 IP，归属单位：XXX` 或 `IP  =>  否`，末尾附 `RESULT: 归属单位或否`。
- JSON 模式字段：
  - `is_cloud`：布尔，是否云主机
  - `provider` / `provider_en`：归属单位（中文/英文）；非云时为 `否` / `No`
  - `ptr`：反向 DNS 主机名（强证据之一）
  - `matched_patterns`：命中的特征关键词
  - `evidence`：RDAP 原始证据（network.name / entity.handle / remark 等）
  - `note`：补充说明（如为何判「否」）
  - `rdap_ok`：RDAP 是否查询成功（失败则结果仅基于 PTR/本地特征，仅供参考）

## 判定原理（多源融合）

1. **RDAP 查询**：统一走 `rdap.arin.net`（可跨区返回 APNIC/RIPE 等数据），失败回退 RIPE/APNIC/AFRINIC/LACNIC。提取网络名称、实体 handle、remarks 备注、实体 org。
2. **反向 DNS（PTR）**：云主机常带品牌后缀（如 `*.compute.amazonaws.com`、`*.bc.googleusercontent.com`、`*.aliyuncs.com`、`*.tencentcloud.com`、`*.myhuaweicloud.com`）。
3. **厂商特征库匹配**：将上述文本拼接后，对内置厂商库（阿里云/腾讯云/华为云/百度智能云/京东云/金山云/火山引擎/UCloud/青云/七牛云/又拍云/白山云/天翼云/移动云/联通云/AWS/Azure/GCP/Oracle/IBM/Cloudflare/DigitalOcean/Vultr/Linode 等）做关键词匹配。
4. **运营商/ISP 特判**：中国电信/联通/移动等仅当命中云专属子域（如 `*.ctyun.cn`、`*.cmcccloud.com`）才判为云，否则判「否」（避免把普通宽带 IP 误判为云主机）。

## 局限与注意

- 依赖公开 RDAP 数据；若运行环境网络受限或该 IP 无 RDAP 记录，`rdap_ok=false`，结论仅参考 PTR，可能不准确。
- 部分 IDC/机房、企业专线、CDN 边缘节点可能不在特征库内，会判「否」或需人工复核 `evidence`。
- 厂商库集中在 `scripts/check_cloud_ip.py` 的 `CLOUD_PROVIDERS` 列表中，可按需增删厂商与特征关键词。
- 纯内网 IP（RFC1918：10/172.16/192.168、100.64 CGNAT、127/169.254 等）无公网 RDAP 数据，会输出「否」。

## 示例

| 输入 IP | 输出 |
|---|---|
| 47.119.176.109 | 阿里云（Alibaba Cloud） |
| 119.29.29.29 | 腾讯云（Tencent Cloud） |
| 121.37.0.1 | 华为云（Huawei Cloud） |
| 8.8.8.8 | 谷歌云（Google Cloud） |
| 52.94.236.248 | 亚马逊云科技（AWS） |
| 13.107.42.14 | 微软云（Microsoft Azure） |
| 202.96.128.86（电信 DNS） | 否 |
