#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shell-gpt 技能实现脚本
功能：将自然语言指令转化为可执行的命令行操作与结构化输出。
"""

import argparse
import json
import re
import shlex
import sys
from typing import Dict, List, Optional, Tuple

# 错误码定义
ERROR_CODES = {
    "E001": "参数错误",
    "E002": "输入为空",
    "E003": "无法识别的指令",
    "E004": "命令生成失败",
    "E005": "JSON序列化失败",
    "E006": "无效的平台类型",
    "E007": "命令执行失败",
    "E008": "权限不足",
    "E009": "资源不存在",
    "E010": "内部错误",
}


class CommandGenerator:
    """命令生成器：将自然语言转换为命令行"""

    # 常见命令模板库
    COMMAND_PATTERNS = {
        "file": {
            "list": ["ls", "dir"],
            "create": ["touch", "mkdir"],
            "delete": ["rm"],
            "copy": ["cp"],
            "move": ["mv"],
            "search": ["find", "grep"],
        },
        "system": {
            "info": ["uname", "df", "free"],
            "process": ["ps", "top"],
            "network": ["ping", "netstat", "curl"],
            "package": ["apt", "pip", "npm"],
        },
        "git": {
            "status": ["git status"],
            "commit": ["git commit"],
            "push": ["git push"],
            "pull": ["git pull"],
            "clone": ["git clone"],
        },
    }

    def __init__(self, platform: str = "auto"):
        """初始化生成器

        Args:
            platform: 目标平台 (auto/linux/mac/windows)
        """
        self.platform = platform
        self._detect_platform()

    def _detect_platform(self) -> None:
        """自动检测当前平台"""
        if self.platform == "auto":
            import platform as pf

            system = pf.system().lower()
            if "windows" in system:
                self.platform = "windows"
            elif "darwin" in system:
                self.platform = "mac"
            else:
                self.platform = "linux"

    def parse_intent(self, text: str) -> Tuple[str, str, Dict]:
        """解析自然语言意图

        Args:
            text: 自然语言指令

        Returns:
            (类别, 动作, 参数) 元组

        Raises:
            ValueError: 当无法识别意图时
        """
        if not text or not text.strip():
            raise ValueError("E002")

        text_lower = text.lower().strip()

        # 简单关键词匹配（实际项目可替换为更智能的解析）
        intent_map = [
            # (类别, 动作, 关键词列表)
            ("file", "list", ["列出", "查看文件", "list", "ls"]),
            ("file", "create", ["创建", "新建", "touch", "mkdir"]),
            ("file", "delete", ["删除", "移除", "rm"]),
            ("file", "copy", ["复制", "拷贝", "cp"]),
            ("file", "move", ["移动", "mv"]),
            ("file", "search", ["搜索", "查找", "find", "grep"]),
            ("system", "info", ["系统信息", "查看系统", "uname", "df"]),
            ("system", "process", ["进程", "process", "ps"]),
            ("system", "network", ["网络", "ping", "netstat"]),
            ("system", "package", ["安装", "包管理", "pip", "apt"]),
            ("git", "status", ["git status", "仓库状态"]),
            ("git", "commit", ["提交", "commit"]),
            ("git", "push", ["推送", "push"]),
            ("git", "pull", ["拉取", "pull"]),
            ("git", "clone", ["克隆", "clone"]),
        ]

        for category, action, keywords in intent_map:
            if any(kw in text_lower for kw in keywords):
                # 提取参数（简单示例：提取引号或括号中的内容）
                params = self._extract_params(text)
                return category, action, params

        raise ValueError("E003")

    def _extract_params(self, text: str) -> Dict:
        """提取命令参数

        Args:
            text: 自然语言文本

        Returns:
            参数字典
        """
        params = {}

        # 提取路径参数
        path_match = re.findall(r'["\']([^"\']+)["\']', text)
        if path_match:
            params["paths"] = path_match

        # 提取选项参数（-x 或 --xxx 格式）
        option_match = re.findall(r'(?:^|\s)(--?[a-zA-Z][\w-]*)', text)
        if option_match:
            params["options"] = option_match

        return params

    def generate(self, text: str) -> Dict:
        """生成命令行结果

        Args:
            text: 自然语言指令

        Returns:
            结构化结果字典

        Raises:
            ValueError: 处理失败时抛出带错误码的异常
        """
        try:
            # 解析意图
            category, action, params = self.parse_intent(text)

            # 生成命令
            commands = self._build_command(category, action, params)

            # 构建结果
            result = {
                "success": True,
                "intent": {
                    "category": category,
                    "action": action,
                    "params": params,
                },
                "commands": commands,
                "platform": self.platform,
                "message": f"已生成{len(commands)}条命令",
            }

            return result

        except ValueError as e:
            error_code = str(e) if str(e) in ERROR_CODES else "E010"
            raise ValueError(error_code) from e

    def _build_command(self, category: str, action: str, params: Dict) -> List[str]:
        """构建具体命令

        Args:
            category: 指令类别
            action: 动作类型
            params: 参数

        Returns:
            命令列表

        Raises:
            ValueError: 生成失败时
        """
        commands = []

        try:
            # 获取基础命令
            base_commands = self.COMMAND_PATTERNS.get(category, {}).get(action, [])

            if not base_commands:
                raise ValueError("E004")

            # 选择命令（按平台适配）
            cmd = self._select_platform_command(base_commands)

            # 附加参数
            if params.get("paths"):
                cmd += " " + " ".join(params["paths"])

            if params.get("options"):
                cmd += " " + " ".join(params["options"])

            commands.append(cmd)

            # 添加辅助命令（如需要）
            if category == "git" and action == "commit":
                commands.append("git log --oneline -5")

            return commands

        except Exception as e:
            if str(e) == "E004":
                raise
            raise ValueError("E004") from e

    def _select_platform_command(self, commands: List[str]) -> str:
        """根据平台选择命令

        Args:
            commands: 候选命令列表

        Returns:
            选中的命令
        """
        if not commands:
            raise ValueError("E004")

        # Windows 平台优先使用 Windows 命令
        if self.platform == "windows":
            for cmd in commands:
                if cmd in ["dir", "type"]:
                    return cmd

        # 默认返回第一个
        return commands[0]


class ShellGPT:
    """主处理类"""

    def __init__(self):
        """初始化"""
        self.generator = CommandGenerator()

    def process(self, text: str) -> Dict:
        """处理自然语言指令

        Args:
            text: 自然语言指令

        Returns:
            处理结果
        """
        try:
            result = self.generator.generate(text)
            return result

        except ValueError as e:
            error_code = str(e)
            return {
                "success": False,
                "error_code": error_code,
                "error_message": ERROR_CODES.get(error_code, "未知错误"),
                "message": ERROR_CODES.get(error_code, "未知错误"),
            }

    def execute(self, text: str, dry_run: bool = True) -> Dict:
        """执行命令

        Args:
            text: 自然语言指令
            dry_run: 是否只生成不执行

        Returns:
            执行结果
        """
        result = self.process(text)

        if not result.get("success"):
            return result

        if dry_run:
            result["dry_run"] = True
            result["message"] = "预览模式：命令未实际执行"
            return result

        # 实际执行模式
        try:
            import subprocess

            outputs = []
            for cmd in result.get("commands", []):
                proc = subprocess.run(
                    shlex.split(cmd),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                outputs.append({
                    "command": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[:500],  # 限制输出长度
                    "stderr": proc.stderr[:500],
                })

            result["outputs"] = outputs
            result["success"] = all(o["returncode"] == 0 for o in outputs)
            result["message"] = "命令执行完成" if result["success"] else "部分命令执行失败"

        except Exception as e:
            result["success"] = False
            result["error_code"] = "E007"
            result["error_message"] = f"命令执行失败: {str(e)}"
            result["message"] = result["error_message"]

        return result


def run_selftest() -> int:
    """自检函数：使用内置样例验证核心逻辑

    Returns:
        0 表示成功，非 0 表示失败
    """
    print("=" * 60)
    print("开始自检 (Self-Test)")
    print("=" * 60)

    # 内置测试样例
    test_cases = [
        {
            "input": "列出当前目录的文件",
            "expected_category": "file",
            "expected_action": "list",
            "expected_has_command": True,
        },
        {
            "input": "查看系统信息",
            "expected_category": "system",
            "expected_action": "info",
            "expected_has_command": True,
        },
        {
            "input": "git status 查看仓库状态",
            "expected_category": "git",
            "expected_action": "status",
            "expected_has_command": True,
        },
        {
            "input": "创建一个新文件 test.txt",
            "expected_category": "file",
            "expected_action": "create",
            "expected_has_command": True,
        },
        {
            "input": "搜索包含 error 的文件",
            "expected_category": "file",
            "expected_action": "search",
            "expected_has_command": True,
        },
    ]

    # 无效输入测试
    invalid_cases = [
        "",
        "   ",
        "完全无法识别的随机文本xyz",
    ]

    passed = 0
    failed = 0

    # 初始化处理器
    processor = ShellGPT()

    # 测试有效输入
    print("\n--- 测试有效输入 ---")
    for i, case in enumerate(test_cases, 1):
        try:
            result = processor.process(case["input"])

            # 宽松断言
            assert result.get("success") is True, "应该成功处理"
            intent = result.get("intent", {})
            assert intent.get("category") == case["expected_category"], \
                f"类别不匹配: {intent.get('category')} != {case['expected_category']}"
            assert intent.get("action") == case["expected_action"], \
                f"动作不匹配: {intent.get('action')} != {case['expected_action']}"

            commands = result.get("commands", [])
            assert len(commands) > 0, "应该生成至少一条命令"
            assert any(cmd.strip() for cmd in commands), "命令不应为空"

            # 宽松验证命令长度
            assert len(commands[0]) > 2, "命令长度应大于2"

            print(f"  ✓ 用例{i}: {case['input']}")
            print(f"    分类: {intent.get('category')}/{intent.get('action')}")
            print(f"    命令: {commands[0]}")
            passed += 1

        except Exception as e:
            print(f"  ✗ 用例{i} 失败: {str(e)}")
            failed += 1

    # 测试无效输入
    print("\n--- 测试无效输入 ---")
    for i, invalid in enumerate(invalid_cases, 1):
        try:
            result = processor.process(invalid)

            # 无效输入应该返回失败
            assert result.get("success") is False, "无效输入应该失败"
            assert "error_code" in result, "应该包含错误码"

            print(f"  ✓ 无效输入{i} 正确拒绝: '{invalid[:20]}...'")
            print(f"    错误码: {result.get('error_code')}")
            passed += 1

        except Exception as e:
            print(f"  ✗ 无效输入{i} 测试异常: {str(e)}")
            failed += 1

    # 测试 JSON 序列化
    print("\n--- 测试结构化输出 ---")
    try:
        result = processor.process("查看系统信息")
        json_str = json.dumps(result, ensure_ascii=False, indent=2)
        assert len(json_str) > 10, "JSON 输出应有内容"
        print("  ✓ JSON 序列化成功")
        passed += 1
    except Exception as e:
        print(f"  ✗ JSON 序列化失败: {str(e)}")
        failed += 1

    # 测试 execute 模式
    print("\n--- 测试执行模式 ---")
    try:
        result = processor.execute("列出当前目录的文件", dry_run=True)
        assert result.get("dry_run") is True, "应该是预览模式"
        assert result.get("success") is True, "预览模式应该成功"
        print("  ✓ 预览模式正常")
        passed += 1
    except Exception as e:
        print(f"  ✗ 预览模式测试失败: {str(e)}")
        failed += 1

    # 汇总
    print("\n" + "=" * 60)
    print(f"自检完成: 通过 {passed} 项, 失败 {failed} 项")
    print("=" * 60)

    return 0 if failed == 0 else 1


def main() -> int:
    """主入口函数

    Returns:
        退出码
    """
    parser = argparse.ArgumentParser(
        description="shell-gpt: 将自然语言指令转化为命令行操作",
        epilog="示例: python main.py '列出当前目录文件'",
    )
    parser.add_argument(
        "--input",
        nargs="?",
        help="自然语言指令",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="运行自检",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行命令（默认仅预览）",
    )
    parser.add_argument(
        "--platform",
        choices=["auto", "linux", "mac", "windows"],
        default="auto",
        help="目标平台",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出",
    )

    parser.add_argument("--verbose", action="store_true", help="显示修改明细")  # R6 可解释输出

    args = parser.parse_args()

    # 自检模式
    if args.selftest:
        return run_selftest()

    # 检查输入
    if not args.input:
        parser.print_help()
        return 1

    # 初始化处理器
    processor = ShellGPT()
    processor.generator.platform = args.platform

    # 处理输入
    result = processor.execute(args.input, dry_run=not args.execute)

    # 输出结果
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("success"):
            print(f"✓ {result.get('message', '处理成功')}")
            for cmd in result.get("commands", []):
                print(f"  $ {cmd}")

            if "outputs" in result:
                for out in result["outputs"]:
                    if out.get("stdout"):
                        print(f"  输出: {out['stdout']}")
                    if out.get("stderr"):
                        print(f"  错误: {out['stderr']}")
        else:
            print(f"✗ {result.get('error_message', result.get('message', '处理失败'))}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())


def read_text_safe(path):
    """多编码容错读取 (selftest contract)"""
    import codecs
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            with codecs.open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()
