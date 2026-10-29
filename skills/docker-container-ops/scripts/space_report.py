#!/usr/bin/env python3
"""space_report.py — dev-004 docker-container-ops：磁盘占用与可回收空间报告

用法:
  python space_report.py [--json]            # 在线模式：调用 docker 命令采集
  python space_report.py --df-input FILE --json   # 离线模式：解析已保存的
                                                  # `docker system df --format json` 输出

本脚本只读不删：输出三级清理建议与命令，任何清理命令都由用户确认后手动执行。
退出码: 0 成功 | 1 可重试（daemon 不可达） | 2 需人工（工具缺失/输入非法）
"""
import argparse
import json
import re
import shutil
import subprocess
import sys

UNIT_BYTES = {"B": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
SIZE_RE = re.compile(r"^([\d.,]+)\s*([kMGT]?B)$")
PCT_RE = re.compile(r"^(.*?)\s*\((\d+)%\)$")


class UserError(Exception):
    def __init__(self, code, message, hints, exit_code):
        super().__init__(message)
        self.code, self.hints, self.exit_code = code, hints, exit_code


def fail(exc):
    print(json.dumps({"ok": False,
                      "error": {"code": exc.code, "message": str(exc),
                                "hints": exc.hints}}, ensure_ascii=False, indent=2))
    return exc.exit_code


def parse_size(text):
    """'1,234kB' / '45.6MB' / '0B' → 字节数；解析失败返回 None。"""
    if not text:
        return None
    m = SIZE_RE.match(text.strip())
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        return int(float(num) * UNIT_BYTES[m.group(2)])
    except (ValueError, KeyError):
        return None


def parse_reclaimable(text):
    """'1.265MB (99%)' → (bytes, percent)。"""
    if not text:
        return None, None
    m = PCT_RE.match(text.strip())
    size_part, pct = (m.group(1), int(m.group(2))) if m else (text, None)
    return parse_size(size_part), pct


def run_docker(argv, timeout=60):
    docker = shutil.which("docker")
    if not docker:
        raise UserError("TOOL_MISSING", "未找到 docker CLI",
                        ["安装 Docker Desktop: https://www.docker.com/products/docker-desktop/",
                         "Linux: curl -fsSL https://get.docker.com | sh"], 2)
    try:
        proc = subprocess.run([docker] + argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UserError("TIMEOUT", "docker 命令超时", ["确认 daemon 响应正常后重试"], 1)
    if proc.returncode != 0:
        low = (proc.stderr or "").lower()
        if "pipe" in low or "cannot connect" in low or "is the docker daemon" in low \
                or "refused" in low:
            raise UserError(
                "DAEMON_UNREACHABLE", "docker daemon 不可达",
                ["Docker Desktop: 启动 Docker Desktop 后重试",
                 "Linux: sudo systemctl start docker",
                 "运行 bash scripts/docker_preflight.sh --json 复核"], 1)
        if "permission denied" in low:
            raise UserError("PERMISSION_DENIED", "无权访问 docker daemon",
                            ["Linux: sudo usermod -aG docker $USER 后重新登录"], 1)
        raise UserError("CMD_FAILED", f"docker 命令失败: {(proc.stderr or '').strip()[:300]}",
                        ["bash scripts/docker_preflight.sh --json 复核环境"], 1)
    return proc.stdout


def parse_ndjson(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def levels_from_df(df_rows):
    """离线模式：仅凭 system df 行给出类型级三级建议。"""
    by_type = {r.get("Type", "?"): r for r in df_rows}
    l1 = by_type.get("Images", {})
    l2 = by_type.get("Containers", {})
    l3 = by_type.get("Local Volumes", {})
    return {
        "level1_low_risk": {
            "scope": "悬空镜像与已停止容器",
            "images_reclaimable": l1.get("Reclaimable"),
            "containers_reclaimable": l2.get("Reclaimable"),
            "commands": ["docker container prune", "docker image prune"],
            "gate": "prune 前向用户复述将删除的范围；无需逐项确认",
        },
        "level2_confirm": {
            "scope": "所有未被容器引用的镜像（含有标签）",
            "commands": ["docker image prune -a"],
            "gate": "确认门：向用户列出将被删的镜像清单并逐类确认",
        },
        "level3_confirm_per_volume": {
            "scope": "未被任何容器引用的卷（可能含数据）",
            "volumes_reclaimable": l3.get("Reclaimable"),
            "commands": ["docker volume ls -f dangling=true", "docker volume rm <NAME>（逐个）"],
            "gate": "硬确认门：卷内数据不可恢复，必须逐卷点名确认，禁止 docker system prune -a --volumes",
        },
    }


def live_details():
    """在线模式：采集镜像/容器/卷明细，计算未用镜像与悬空卷清单。"""
    images = parse_ndjson(run_docker(["images", "--format", "{{json .}}"]))
    containers = parse_ndjson(run_docker(["ps", "-a", "--size", "--format", "{{json .}}"]))
    dangling_vols = parse_ndjson(
        run_docker(["volume", "ls", "-f", "dangling=true", "--format", "{{json .}}"]))
    dangling_imgs = [i for i in images if "<none>" in (i.get("Repository", "")
                                                       + ":" + i.get("Tag", ""))]

    used_refs = set()
    stopped = []
    for c in containers:
        used_refs.add(str(c.get("Image", "")))
        used_refs.add(str(c.get("ID", ""))[:12])
        if c.get("State") in ("exited", "created", "dead"):
            stopped.append(c)

    def img_ref(i):
        return f'{i.get("Repository", "")}:{i.get("Tag", "")}'

    unused = [i for i in images
              if img_ref(i) not in used_refs and str(i.get("ID", ""))[:12] not in used_refs]

    def img_size_bytes(i):
        return parse_size(i.get("Size", "")) or 0

    return {
        "dangling_images": [{"id": i.get("ID"), "size": i.get("Size")}
                            for i in dangling_imgs],
        "unused_images_count": len(unused),
        "unused_images_bytes": sum(img_size_bytes(i) for i in unused),
        "stopped_containers": [{"names": c.get("Names"), "image": c.get("Image"),
                                "state": c.get("State"), "size": c.get("Size")}
                               for c in stopped],
        "dangling_volumes": [{"name": v.get("Name"), "driver": v.get("Driver")}
                             for v in dangling_vols],
    }


def main():
    ap = argparse.ArgumentParser(
        description="Docker 磁盘占用与可回收空间三级报告（只读，不执行清理）")
    ap.add_argument("--df-input", help="离线：docker system df --format json 的输出文件")
    ap.add_argument("--json", action="store_true", help="输出 JSON 信封")
    args = ap.parse_args()

    data = {"mode": "offline" if args.df_input else "live", "df": [], "levels": {}}
    try:
        if args.df_input:
            try:
                with open(args.df_input, encoding="utf-8") as fh:
                    df_rows = parse_ndjson(fh.read())
            except OSError as exc:
                raise UserError("INVALID_INPUT", f"无法读取输入: {exc}",
                                ["确认文件路径正确"], 2)
            if not df_rows:
                raise UserError("PARSE_ERROR",
                                "输入未解析出 system df 行（应为 --format json 的 NDJSON）",
                                ["生成命令: docker system df --format json"], 2)
            data["df"] = df_rows
            data["levels"] = levels_from_df(df_rows)
        else:
            df_rows = parse_ndjson(run_docker(["system", "df", "--format", "json"]))
            if not df_rows:
                raise UserError("CMD_FAILED", "docker system df 未返回数据",
                                ["bash scripts/docker_preflight.sh --json 复核"], 1)
            data["df"] = df_rows
            data["levels"] = levels_from_df(df_rows)
            data["details"] = live_details()

        total_reclaim, notes = 0, []
        for row in data["df"]:
            b, pct = parse_reclaimable(row.get("Reclaimable", ""))
            row["reclaimable_bytes"] = b
            row["reclaimable_pct"] = pct
            if b:
                total_reclaim += b
                notes.append(f'{row.get("Type")}: 可回收 {b} 字节'
                             + (f'（{pct}%）' if pct is not None else ""))
        data["total_reclaimable_bytes"] = total_reclaim
        data["warnings"] = [
            "本脚本只读；任何 prune/rm 命令执行前必须过确认门",
            "禁止直接执行 docker system prune -a --volumes",
        ] if total_reclaim else ["当前无可回收空间"]

        if args.json:
            print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
        else:
            print("== docker system df ==")
            for row in data["df"]:
                print(f'  {row.get("Type")}: count={row.get("TotalCount")} '
                      f'size={row.get("Size")} reclaimable={row.get("Reclaimable")}')
            if "details" in data:
                d = data["details"]
                print(f'悬空镜像 {len(d["dangling_images"])} 个 ｜ '
                      f'未用镜像 {d["unused_images_count"]} 个 '
                      f'({d["unused_images_bytes"]} B) ｜ '
                      f'停止容器 {len(d["stopped_containers"])} 个 ｜ '
                      f'悬空卷 {len(d["dangling_volumes"])} 个')
            print("三级清理建议：L1 悬空镜像/停止容器 → L2 未用镜像(确认) → "
                  "L3 悬空卷(逐卷硬确认)")
            for w in data["warnings"]:
                print(f'  ⚠ {w}')
        return 0
    except UserError as exc:
        return fail(exc)


if __name__ == "__main__":
    sys.exit(main())
