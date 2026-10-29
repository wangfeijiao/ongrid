# -*- coding: utf-8 -*-
"""v1.0.2 自检：SKILL.md 结构一致性（首次引导 + 与顾问分工 + 版本号）。
用法: python selftest.py（预期输出"自检通过：N/N 断言全部通过"）"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "SKILL.md")


def main():
    passed = []
    text = open(SKILL, encoding="utf-8").read()

    # frontmatter
    fm = re.match(r"^---\n(.*?)\n---", text, re.S)
    passed.append(("存在 frontmatter", bool(fm)))
    fmb = fm.group(1) if fm else ""
    passed.append(("版本号为 1.0.2", 'version: "1.0.2"' in fmb))
    passed.append(("display_name 仍为 电脑自动化全流程助手",
                   "display_name: 电脑自动化全流程助手" in fmb))

    # 首次引导章节
    passed.append(("含『首次引导』章节", "## 首次引导" in text))
    passed.append(("首次引导说明打开即触发", "打开技能即触发" in text))
    passed.append(("首次引导只做选择题（不扫描）", "只读、不扫描、不写任何文件" in text))

    # 与顾问分工章节
    passed.append(("含与顾问分工章节", "与「电脑自动化顾问」系列的关系" in text))
    passed.append(("明确本技能是执行器", "落地执行器" in text))
    passed.append(("明确顾问诊断/studio 落地", "诊断归顾问" in text and "落地归 studio" in text))

    # 不抢活：仍强调确认闸门
    passed.append(("仍保留确认硬闸门", "确认（硬闸门" in text or "硬闸门" in text))

    fails = [n for n, ok in passed if not ok]
    print("自检通过：%d/%d 断言全部通过" % (len(passed) - len(fails), len(passed)))
    if fails:
        for n in fails:
            print("  FAIL: %s" % n)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
