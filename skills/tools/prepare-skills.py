#!/usr/bin/env python3
"""prepare-skills.py — 生成带 keyword 激活配置的 ongrid 技能部署包。

源：本仓库 skills/ 下的原样技能（每个目录含 SKILL.md）。
输出：部署树（默认 .cache/skills-deploy/）+ tar.gz（默认 .cache/ongrid-ops-skills.tar.gz）。

部署树中每个技能的 SKILL.md 会做两项适配：

1. frontmatter 注入激活配置（否则 activation 缺省 = always，全文注入每轮提示词）：

    activation:
      mode: keyword
      keywords: [...]

2. frontmatter 的 name 归一化为 snake_case（ongrid 加载时的既定规则，
   提前归一可消除启动日志里的 name_normalized 告警）。

原因：78 个技能正文合计约 570KB，必须让技能仅在用户消息命中关键词子串
（大小写不敏感）时注入系统提示词。

用法（在仓库根目录执行）：
    python skills/tools/prepare-skills.py
    python skills/tools/prepare-skills.py --out DIR --tar FILE
    python skills/tools/prepare-skills.py --no-tar
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "skills"

# 每个技能的激活关键词（2-4 字短词为主，大小写不敏感子串匹配用户消息）。
# 覆盖技能的核心触发场景；新增技能时必须同步补充，否则脚本报缺。
KEYWORDS: dict[str, list[str]] = {
    "alert-noise-governance": ["告警噪音", "误报", "告警太多", "告警刷屏", "静默", "去重"],
    "alert-rule-design-hckp": ["告警规则", "告警设计", "阈值", "规则配置"],
    "alert-tiering-hckp": ["告警分级", "P0", "P1", "优先级", "告警等级"],
    "automations": ["一键脚本", "流程自动化", "重复操作", "脚本自动化"],
    "baige-guzhang-fupan": ["故障复盘", "复盘报告", "生产故障"],
    "canary-plan-hckp": ["灰度发布", "灰度方案", "发布批次"],
    "canary-release-hckp": ["灰度", "金丝雀", "灰度批次"],
    "capacity-plan-hckp": ["容量规划", "资源规划", "扩容", "峰值"],
    "cci-cd-pipeline": ["GitHub Actions", "GitLab CI", "流水线配置", "CI 配置"],
    "cicd-standard-hckp": ["流水线规范", "CI/CD 规范", "构建测试部署"],
    "cicd-troubleshooter": ["流水线失败", "构建失败", "CI 排障", "部署失败", "构建日志"],
    "cloud-ip-checker": ["IP 归属", "云主机 IP", "IP 鉴别"],
    "cron-dashboard-status": ["定时任务", "cron", "任务状态", "漏跑"],
    "cross-server-backup": ["rsync", "跨服务器备份", "异地备份", "灾备"],
    "dashboard": ["看板", "仪表盘", "数据大屏", "KPI 卡片"],
    "data-backup-checklist": ["备份核查", "备份清单", "恢复演练", "完整性校验"],
    "database-ops-assistant-kb": ["数据库运维", "慢查询", "索引", "连接池", "主从复制"],
    "db-backup-restore": ["数据库备份", "数据恢复", "库坏了", "备份恢复"],
    "dependency-vuln-scanner": ["依赖漏洞", "CVE", "依赖扫描"],
    "docker-assistant": ["Docker", "docker 命令", "容器", "镜像"],
    "docker-command-generator": ["docker 命令", "compose", "容器命令"],
    "docker-container-ops": ["容器清理", "容器管理", "磁盘占用", "容器管家"],
    "docker-handler": ["容器运维", "docker 管理", "容器网络"],
    "fastapi-ssh-deploy": ["FastAPI 部署", "systemd 服务", "接口部署"],
    "grafana-dashboard-gen": ["Grafana 面板", "监控看板", "Dashboard 生成", "仪表盘生成"],
    "grafana-queries": ["Grafana 告警", "告警快照", "Grafana 查询", "着火告警"],
    "incident-review-aid": ["故障复盘", "复盘文档", "根因归因", "5Why"],
    "ir-plan-hckp": ["安全事件", "应急响应", "入侵处置"],
    "k8s-trouble-hckp": ["K8s", "Kubernetes", "Pod", "集群排错"],
    "linux-hardening-hckp": ["安全加固", "SSH 加固", "防火墙", "加固清单"],
    "linux-security-auditor-hckp": ["安全基线", "配置审计", "CIS", "加固审计"],
    "log-analysis-hckp": ["日志分析", "日志排查", "异常溯源"],
    "log-analysis-troubleshooting": ["日志排查", "日志异常", "日志合并"],
    "log-anomaly-analyzer": ["日志异常", "错误聚合", "日志突增"],
    "log-anomaly-detection": ["日志异常检测", "错误率", "ERROR 趋势"],
    "log-fault-analyzer": ["报错翻译", "日志诊断", "traceback", "异常解释"],
    "log-rca-timeline": ["日志根因", "故障时间线", "日志时间线", "根因定位"],
    "lynxce-incident-postmortem": ["线上故障", "复盘报告", "事故复盘"],
    "metago-deploy-engineer": ["部署方案", "发布流程", "上线流程"],
    "metago-monitoring-engineer": ["监控指标", "监控建设", "指标设计"],
    "network-access-block-diagnosis": ["网站打不开", "网页打不开", "502", "WAF", "人机验证"],
    "network-probe": ["网络检测", "ping", "断网", "私接路由", "网络诊断"],
    "network-troubleshooter-kb": ["网络排障", "断网", "DNS", "IP 冲突", "VPN", "网速慢"],
    "nginx-assistant": ["nginx", "location", "rewrite", "502", "504", "反向代理"],
    "nginx-check-hckp": ["nginx 配置检查", "nginx -t", "配置校验"],
    "nginx-config-auditor-hckp": ["nginx 审计", "配置安全", "nginx 加固"],
    "nl-to-shell-command": ["自然语言转命令", "命令生成", "shell 命令"],
    "nuodun-server-defense": ["木马", "后门", "杀毒", "病毒查杀"],
    "observability-and-instrumentation": ["可观测性", "结构化日志", "RED 指标", "链路追踪"],
    "observability-engineer-kb": ["监控体系", "可观测性建设", "告警治理", "大盘设计", "值班"],
    "ops-assistant": ["运维", "线上故障", "服务器告警", "发布部署", "监控配置"],
    "ops-inspector": ["服务器巡检", "服务器体检", "CPU 高", "内存高", "磁盘满"],
    "pc-automation-studio": ["电脑自动化", "重复工作", "自动化方案"],
    "pc-cleaner": ["磁盘清理", "空间不足", "清理垃圾", "C盘"],
    "pentest-assistant": ["渗透测试", "pentest", "漏洞报告", "CVSS"],
    "pentest-checklist": ["渗透测试清单", "授权范围", "测试核对"],
    "perf-stress-bottleneck": ["性能压测", "瓶颈定位", "并发测试"],
    "perf-stress-hckp": ["压测", "TPS", "性能测试", "接口压测"],
    "phosphor": ["Ruby", "DTrace", "运行时追踪", "性能剖析"],
    "port-probe-hckp": ["端口探测", "端口连通", "TCP 探测"],
    "postmortem-report-hckp": ["故障复盘", "复盘报告", "改进项"],
    "release-check-skill": ["上线检查", "回滚方案", "发布检查"],
    "release-rollback-plan-hckp": ["回滚预案", "版本回退", "发布风险"],
    "release-rollback-planner": ["回滚方案", "变更单", "回滚触发线"],
    "repeat-work-automation": ["重复工作", "自动化", "RPA"],
    "security-baseline-check-hckp": ["安全基线", "合规核查", "整改项"],
    "server-assistant": ["服务器", "SSH", "新机初始化", "systemd", "免密登录"],
    "server-checklist-hckp": ["服务器巡检", "巡检清单", "日周月巡检"],
    "server-setup": ["服务器配置", "Nginx 配置", "TLS", "安全配置"],
    "skill-incident-postmortem-note": ["事故复盘", "复盘纪要"],
    "smart-disk-cleaner": ["磁盘清理", "磁盘满", "空间不足", "缓存清理", "C盘"],
    "sql-slow-query-hckp": ["慢查询", "执行计划", "SQL 慢", "索引"],
    "src-vuln-scanner": ["漏洞扫描", "SQL 注入", "XSS", "授权扫描"],
    "tencentcloud-deploy-web": ["腾讯云", "域名解析", "证书", "EdgeOne"],
    "vul-analyse": ["漏扫报告", "漏洞分析", "漏扫"],
    "workspace-ops-scripts": ["M365", "批量自动化", "脚本运维"],
    "ym-docker-management": ["docker 管理", "运维部署"],
    "ym-setup-wizard-generator": ["安装向导", "setup wizard", "部署向导"],
}


def skill_dirs() -> list[Path]:
    return sorted(
        (d for d in SRC_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()),
        key=lambda d: d.name,
    )


_NON_SNAKE = re.compile(r"[^a-z0-9_]+")


def normalize_snake(value: str) -> str:
    """与 ongrid chatruntime 的 name 归一化规则一致：非 [a-z0-9_] 字符 → _。"""
    out = _NON_SNAKE.sub("_", value.strip().lower()).strip("_")
    return out or "unnamed"


def inject_activation(text: str, kws: list[str]) -> tuple[str, bool]:
    """在 SKILL.md 的 frontmatter 尾部注入 activation 块，并把 name 归一化为 snake_case。

    返回 (新文本, 是否注入了 activation)。已存在 activation 字段时不覆盖。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing leading frontmatter fence '---'")
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        raise ValueError("frontmatter not closed by '---'")

    has_activation = False
    fm_index: dict[str, int] = {}
    for i in range(1, close):
        key, sep, val = lines[i].partition(":")
        key = key.strip()
        if not sep or not key:
            continue
        fm_index[key] = i
        if key == "activation":
            has_activation = True
        elif key == "name":
            raw = val.strip().strip('"').strip("'")
            norm = normalize_snake(raw)
            if norm != raw:
                lines[i] = f"name: {norm}"

    # ongrid 要求 frontmatter 必须有非空 description；少数技能只带
    # description_zh / description_en —— 从 description_zh 镜像一份出来。
    desc_val = None
    if "description" in fm_index:
        desc_val = lines[fm_index["description"]].partition(":")[2].strip()
    if desc_val in (None, "", '""', "''"):
        if "description_zh" in fm_index:
            zh_val = lines[fm_index["description_zh"]].partition(":")[2].strip()
            if zh_val:
                lines.insert(fm_index["description_zh"] + 1, f"description: {zh_val}")
                close += 1
        elif desc_val is None:
            raise ValueError("no description / description_zh in frontmatter")

    if has_activation:
        return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), False

    block = ["activation:", "  mode: keyword", "  keywords:"]
    for kw in kws:
        escaped = kw.replace("\\", "\\\\").replace('"', '\\"')
        block.append(f'    - "{escaped}"')
    new_lines = lines[:close] + block + lines[close:]
    out = "\n".join(new_lines)
    if text.endswith("\n"):
        out += "\n"
    return out, True


def main() -> int:
    ap = argparse.ArgumentParser(description="generate keyword-activated deploy pack")
    ap.add_argument("--out", default=str(REPO_ROOT / ".cache" / "skills-deploy"),
                    help="deploy tree output dir (default: .cache/skills-deploy)")
    ap.add_argument("--tar", default=str(REPO_ROOT / ".cache" / "ongrid-ops-skills.tar.gz"),
                    help="tarball output path (default: .cache/ongrid-ops-skills.tar.gz)")
    ap.add_argument("--no-tar", action="store_true", help="skip tarball creation")
    args = ap.parse_args()

    skills = skill_dirs()
    missing = [d.name for d in skills if d.name not in KEYWORDS]
    if missing:
        print("ERROR: no keywords configured for: " + ", ".join(missing), file=sys.stderr)
        return 1
    extra = [k for k in KEYWORDS if k not in {d.name for d in skills}]
    if extra:
        print("WARN: keyword entries without skill dir: " + ", ".join(sorted(extra)))

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    injected = 0
    for d in skills:
        dst = out / d.name
        shutil.copytree(d, dst)
        md = dst / "SKILL.md"
        text = md.read_text(encoding="utf-8")
        new_text, did = inject_activation(text, KEYWORDS[d.name])
        if did:
            md.write_text(new_text, encoding="utf-8")
            injected += 1
        else:
            print(f"WARN: {d.name} already declares activation; left as-is")

    # verify every generated SKILL.md carries keyword activation + a non-empty description
    bad = []
    for d in skills:
        t = (out / d.name / "SKILL.md").read_text(encoding="utf-8")
        if "activation:" not in t or "mode: keyword" not in t:
            bad.append(d.name + ":activation")
        m = re.search(r"^description:\s*(\S.*)?$", t, re.M)
        if not m:
            bad.append(d.name + ":description-missing")
        elif (m.group(1) or "").strip() in ("", '""', "''"):
            bad.append(d.name + ":description-empty")
    if bad:
        print("ERROR: verification failed for: " + ", ".join(bad), file=sys.stderr)
        return 1

    if not args.no_tar:
        tar_path = Path(args.tar)
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "w:gz") as tf:
            for d in skills:
                tf.add(out / d.name, arcname=d.name)
        print(f"tar: {tar_path} ({tar_path.stat().st_size / 1024 / 1024:.2f} MB)")

    print(f"done: {len(skills)} skills -> {out} (activation injected: {injected})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
