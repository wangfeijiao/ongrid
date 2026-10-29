#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 免费兼容版提示脚本（原 402 付费客户端已移除）。

本技能在 WorkBuddy 为免费本地版，无需走支付宝 402 网关。
如需按次付费的最新规范包与资产，请到 SkillHub 搜索同名技能：canary-plan-hckp
"""
import sys

if __name__ == "__main__":
    sys.stdout.write(
        "本技能为 WorkBuddy 免费版，本地脚本可直接使用，无需付费。\n"
        f"付费按次版（402 网关）请到 SkillHub 搜索：canary-plan-hckp\n"
    )
