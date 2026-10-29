#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
world-of-m365 — M365 运维脚本化工具集（独立实现）

本脚本为 clean-room 重写，仅依据功能规格实现核心逻辑。
覆盖能力：Exchange Online / Teams / SharePoint Online / 安全合规 / 用户生命周期。
提供命令行接口与内置样例数据的离线自检（--selftest）。

仅使用 Python 标准库，无第三方依赖。
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


# ============================================================
# 错误码定义（E001-E010）
# ============================================================
ERROR_CODES = {
    "E001": "参数缺失或格式非法",
    "E002": "输入数据为空",
    "E003": "用户不存在或已禁用",
    "E004": "邮箱地址格式非法",
    "E005": "团队不存在",
    "E006": "站点不存在或无访问权限",
    "E007": "审计记录查询失败",
    "E008": "许可证操作不支持（应通过官方门户）",
    "E009": "破坏性操作被拒绝",
    "E010": "内部逻辑错误或未知异常",
}


class M365Error(Exception):
    """M365 运维异常，携带错误码。"""

    def __init__(self, code: str, message: Optional[str] = None):
        self.code = code
        self.message = message or ERROR_CODES.get(code, "未知错误")
        super().__init__(f"[{code}] {self.message}")


# ============================================================
# 数据校验工具
# ============================================================
def validate_email(email: str) -> bool:
    """校验邮箱地址基本格式（宽松校验）。"""
    if not email or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


def validate_non_empty(data: Any, err_code: str = "E002") -> None:
    """校验数据非空，否则抛出指定错误码异常。"""
    if data is None:
        raise M365Error(err_code)
    if isinstance(data, (list, dict, str)) and len(data) == 0:
        raise M365Error(err_code)


# ============================================================
# 核心数据模型（模拟 M365 资源）
# ============================================================
class M365User:
    """M365 用户对象。"""

    def __init__(self, user_id: str, display_name: str, email: str, enabled: bool = True):
        self.user_id = user_id
        self.display_name = display_name
        self.email = email
        self.enabled = enabled
        self.groups: List[str] = []
        self.teams: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "email": self.email,
            "enabled": self.enabled,
            "groups": list(self.groups),
            "teams": list(self.teams),
        }


class M365Team:
    """Teams 团队对象。"""

    def __init__(self, team_id: str, display_name: str, description: str = ""):
        self.team_id = team_id
        self.display_name = display_name
        self.description = description
        self.members: List[str] = []  # 存储 user_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_id": self.team_id,
            "display_name": self.display_name,
            "description": self.description,
            "members": list(self.members),
        }


class M365Site:
    """SharePoint Online 站点对象。"""

    def __init__(self, site_id: str, url: str, title: str):
        self.site_id = site_id
        self.url = url
        self.title = title
        self.permissions: Dict[str, List[str]] = {}  # user_id -> [role]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "url": self.url,
            "title": self.title,
            "permissions": {k: list(v) for k, v in self.permissions.items()},
        }


# ============================================================
# 模拟数据仓库（内存版）
# ============================================================
class M365Repository:
    """内存版 M365 数据仓库，模拟真实租户数据。"""

    def __init__(self):
        self.users: Dict[str, M365User] = {}
        self.teams: Dict[str, M365Team] = {}
        self.sites: Dict[str, M365Site] = {}
        self.audit_logs: List[Dict[str, Any]] = []

    # ---------- 用户管理 ----------
    def add_user(self, user: M365User) -> None:
        if not validate_email(user.email):
            raise M365Error("E004", f"非法邮箱: {user.email}")
        if user.user_id in self.users:
            raise M365Error("E001", f"用户已存在: {user.user_id}")
        self.users[user.user_id] = user

    def get_user(self, user_id: str) -> M365User:
        if user_id not in self.users:
            raise M365Error("E003", f"用户不存在: {user_id}")
        user = self.users[user_id]
        if not user.enabled:
            raise M365Error("E003", f"用户已禁用: {user_id}")
        return user

    def disable_user(self, user_id: str) -> None:
        if user_id not in self.users:
            raise M365Error("E003", f"用户不存在: {user_id}")
        self.users[user_id].enabled = False

    def list_users(self) -> List[M365User]:
        return list(self.users.values())

    # ---------- 团队管理 ----------
    def add_team(self, team: M365Team) -> None:
        if team.team_id in self.teams:
            raise M365Error("E001", f"团队已存在: {team.team_id}")
        self.teams[team.team_id] = team

    def get_team(self, team_id: str) -> M365Team:
        if team_id not in self.teams:
            raise M365Error("E005", f"团队不存在: {team_id}")
        return self.teams[team_id]

    def add_team_member(self, team_id: str, user_id: str) -> None:
        team = self.get_team(team_id)
        user = self.get_user(user_id)
        if user_id not in team.members:
            team.members.append(user_id)
        if team_id not in user.teams:
            user.teams.append(team_id)

    def list_team_members(self, team_id: str) -> List[str]:
        team = self.get_team(team_id)
        return list(team.members)

    # ---------- SharePoint 站点 ----------
    def add_site(self, site: M365Site) -> None:
        if site.site_id in self.sites:
            raise M365Error("E001", f"站点已存在: {site.site_id}")
        self.sites[site.site_id] = site

    def get_site(self, site_id: str) -> M365Site:
        if site_id not in self.sites:
            raise M365Error("E006", f"站点不存在: {site_id}")
        return self.sites[site_id]

    def set_site_permission(self, site_id: str, user_id: str, role: str) -> None:
        site = self.get_site(site_id)
        self.get_user(user_id)
        if user_id not in site.permissions:
            site.permissions[user_id] = []
        if role not in site.permissions[user_id]:
            site.permissions[user_id].append(role)

    # ---------- 审计日志 ----------
    def add_audit_log(self, log: Dict[str, Any]) -> None:
        log["timestamp"] = datetime.utcnow().isoformat()
        self.audit_logs.append(log)

    def query_audit_logs(self, user_id: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = []
        for log in self.audit_logs:
            log_time = datetime.fromisoformat(log["timestamp"])
            if log_time < cutoff:
                continue
            if user_id and log.get("user_id") != user_id:
                continue
            result.append(log)
        return result


# ============================================================
# 核心业务服务（M365 运维操作）
# ============================================================
class M365Service:
    """M365 运维服务，封装核心业务逻辑。"""

    def __init__(self, repo: M365Repository):
        self.repo = repo

    # ---------- Exchange Online ----------
    def batch_create_mailbox(self, users: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """批量创建邮箱（模拟：创建用户并启用邮箱）。"""
        validate_non_empty(users, "E002")
        results = []
        for item in users:
            try:
                user_id = item["user_id"]
                email = item["email"]
                display_name = item.get("display_name", user_id)
                if not validate_email(email):
                    raise M365Error("E004")
                user = M365User(user_id, display_name, email, enabled=True)
                self.repo.add_user(user)
                results.append({"user_id": user_id, "status": "created"})
            except M365Error as e:
                results.append({"user_id": item.get("user_id", "unknown"), "status": "failed", "error": e.code})
        return results

    def configure_forwarding(self, user_id: str, forward_to: str) -> Dict[str, Any]:
        """配置邮件转发规则。"""
        user = self.repo.get_user(user_id)
        if not validate_email(forward_to):
            raise M365Error("E004", f"目标邮箱非法: {forward_to}")
        # 模拟配置成功
        self.repo.add_audit_log({
            "action": "configure_forwarding",
            "user_id": user_id,
            "detail": {"forward_to": forward_to, "email": user.email},
        })
        return {"user_id": user_id, "forward_to": forward_to, "status": "configured"}

    # ---------- Teams 管理 ----------
    def batch_create_teams(self, teams: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """批量创建 Teams 团队。"""
        validate_non_empty(teams, "E002")
        results = []
        for item in teams:
            try:
                team_id = item["team_id"]
                display_name = item["display_name"]
                description = item.get("description", "")
                team = M365Team(team_id, display_name, description)
                self.repo.add_team(team)
                results.append({"team_id": team_id, "status": "created"})
            except M365Error as e:
                results.append({"team_id": item.get("team_id", "unknown"), "status": "failed", "error": e.code})
        return results

    def add_team_members_batch(self, team_id: str, user_ids: List[str]) -> Dict[str, Any]:
        """批量添加团队成员。"""
        team = self.repo.get_team(team_id)
        added, failed = [], []
        for uid in user_ids:
            try:
                self.repo.add_team_member(team_id, uid)
                added.append(uid)
            except M365Error as e:
                failed.append({"user_id": uid, "error": e.code})
        self.repo.add_audit_log({
            "action": "add_team_members",
            "team_id": team_id,
            "detail": {"added": added, "failed": failed},
        })
        return {"team_id": team_id, "added": added, "failed": failed, "member_count": len(team.members)}

    def export_team_members(self, team_id: str) -> List[Dict[str, str]]:
        """导出团队成员列表（含用户信息）。"""
        team = self.repo.get_team(team_id)
        result = []
        for uid in team.members:
            try:
                user = self.repo.get_user(uid)
                result.append({"user_id": uid, "display_name": user.display_name, "email": user.email})
            except M365Error:
                result.append({"user_id": uid, "display_name": "未知", "email": "unknown"})
        return result

    # ---------- SharePoint Online ----------
    def batch_adjust_permissions(self, site_id: str, permissions: Dict[str, str]) -> Dict[str, Any]:
        """批量调整站点权限。permissions: {user_id: role}"""
        site = self.repo.get_site(site_id)
        adjusted = []
        for uid, role in permissions.items():
            try:
                self.repo.set_site_permission(site_id, uid, role)
                adjusted.append({"user_id": uid, "role": role})
            except M365Error as e:
                adjusted.append({"user_id": uid, "role": role, "error": e.code})
        self.repo.add_audit_log({
            "action": "adjust_site_permissions",
            "site_id": site_id,
            "detail": {"adjusted": adjusted},
        })
        return {"site_id": site_id, "adjusted": adjusted, "permission_count": len(site.permissions)}

    # ---------- 安全与合规 ----------
    def query_login_logs(self, user_id: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        """查询登录日志（模拟）。"""
        if days < 1 or days > 365:
            raise M365Error("E001", "天数范围应为 1-365")
        return self.repo.query_audit_logs(user_id=user_id, days=days)

    def configure_retention_label(self, label_name: str, retention_days: int) -> Dict[str, Any]:
        """配置保留标签。"""
        if retention_days < 1 or retention_days > 3650:
            raise M365Error("E001", "保留天数范围应为 1-3650")
        self.repo.add_audit_log({
            "action": "configure_retention_label",
            "detail": {"label_name": label_name, "retention_days": retention_days},
        })
        return {"label_name": label_name, "retention_days": retention_days, "status": "configured"}

    # ---------- 用户生命周期 ----------
    def batch_update_users(self, updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量更新用户属性。"""
        validate_non_empty(updates, "E002")
        results = []
        for item in updates:
            user_id = item["user_id"]
            try:
                user = self.repo.get_user(user_id)
                if "display_name" in item:
                    user.display_name = item["display_name"]
                if "email" in item:
                    if not validate_email(item["email"]):
                        raise M365Error("E004")
                    user.email = item["email"]
                results.append({"user_id": user_id, "status": "updated"})
            except M365Error as e:
                results.append({"user_id": user_id, "status": "failed", "error": e.code})
        return results

    def cleanup_orphan_accounts(self, active_user_ids: List[str]) -> Dict[str, Any]:
        """清理孤儿账号（模拟：仅标记禁用，不执行删除）。"""
        validate_non_empty(active_user_ids, "E002")
        all_users = self.repo.list_users()
        active_set = set(active_user_ids)
        orphan_ids = [u.user_id for u in all_users if u.user_id not in active_set]
        for uid in orphan_ids:
            self.repo.disable_user(uid)
        self.repo.add_audit_log({
            "action": "cleanup_orphan_accounts",
            "detail": {"orphan_count": len(orphan_ids), "orphan_ids": orphan_ids},
        })
        return {"orphan_count": len(orphan_ids), "orphan_ids": orphan_ids, "status": "disabled"}


# ============================================================
# 自检模块（内置硬编码样例数据，离线运行）
# ============================================================
def run_selftest() -> int:
    """运行内置自检，验证核心逻辑。返回 0 表示通过，非 0 表示失败。"""
    print("=" * 60)
    print("world-of-m365 自检开始 (selftest)")
    print("=" * 60)

    try:
        # ---------- 准备测试仓库 ----------
        repo = M365Repository()
        service = M365Service(repo)

        # ---------- 1. 批量创建邮箱 ----------
        print("\n[1/6] 批量创建邮箱...")
        users_data = [
            {"user_id": "u001", "email": "alice@example.com", "display_name": "Alice"},
            {"user_id": "u002", "email": "bob@example.com", "display_name": "Bob"},
            {"user_id": "u003", "email": "carol@example.com", "display_name": "Carol"},
            {"user_id": "u004", "email": "invalid-email", "display_name": "Bad User"},
        ]
        results = service.batch_create_mailbox(users_data)
        created_count = sum(1 for r in results if r["status"] == "created")
        failed_count = sum(1 for r in results if r["status"] == "failed")
        assert created_count >= 3, f"应至少创建 3 个用户，实际 {created_count}"
        assert failed_count >= 1, "应至少有 1 个非法邮箱被拒绝"
        print(f"  ✓ 创建成功 {created_count} 个，失败 {failed_count} 个（非法邮箱被拒绝）")

        # ---------- 2. 配置邮件转发 ----------
        print("\n[2/6] 配置邮件转发...")
        fwd_result = service.configure_forwarding("u001", "manager@example.com")
        assert fwd_result["status"] == "configured"
        assert fwd_result["forward_to"] == "manager@example.com"
        print(f"  ✓ 转发配置成功: {fwd_result['user_id']} -> {fwd_result['forward_to']}")

        # ---------- 3. 批量创建 Teams 并添加成员 ----------
        print("\n[3/6] 批量创建 Teams 并添加成员...")
        teams_data = [
            {"team_id": "t001", "display_name": "项目A", "description": "项目A团队"},
            {"team_id": "t002", "display_name": "项目B", "description": "项目B团队"},
        ]
        team_results = service.batch_create_teams(teams_data)
        assert sum(1 for r in team_results if r["status"] == "created") == 2
        member_result = service.add_team_members_batch("t001", ["u001", "u002", "u003"])
        assert member_result["member_count"] == 3, f"成员数应为 3，实际 {member_result['member_count']}"
        assert len(member_result["added"]) == 3
        exported = service.export_team_members("t001")
        assert len(exported) == 3, f"导出成员应为 3，实际 {len(exported)}"
        print(f"  ✓ 团队创建成功，t001 添加成员 {len(exported)} 人")

        # ---------- 4. SharePoint 权限调整 ----------
        print("\n[4/6] SharePoint 站点权限调整...")
        site = M365Site("s001", "https://contoso.sharepoint.com/sites/projA", "项目A站点")
        repo.add_site(site)
        perm_result = service.batch_adjust_permissions("s001", {"u001": "成员", "u002": "所有者"})
        assert perm_result["permission_count"] == 2, f"权限数应为 2，实际 {perm_result['permission_count']}"
        assert len(site.permissions["u001"]) == 1
        print(f"  ✓ 权限调整成功，共 {perm_result['permission_count']} 条权限")

        # ---------- 5. 审计日志与合规 ----------
        print("\n[5/6] 审计日志查询与合规配置...")
        # 手动添加几条审计日志（模拟历史数据）
        for i in range(5):
            repo.add_audit_log({
                "action": "login",
                "user_id": "u001" if i % 2 == 0 else "u002",
                "detail": {"ip": f"10.0.0.{i+1}"},
            })
        logs = service.query_login_logs(user_id="u001", days=30)
        assert len(logs) >= 2, f"u001 的登录日志应至少 2 条，实际 {len(logs)}"
        label_result = service.configure_retention_label("财务记录", 365)
        assert label_result["retention_days"] == 365
        print(f"  ✓ 审计日志查询到 {len(logs)} 条，保留标签配置成功")

        # ---------- 6. 用户生命周期管理 ----------
        print("\n[6/6] 用户生命周期管理...")
        # 批量更新
        update_result = service.batch_update_users([
            {"user_id": "u001", "display_name": "Alice A."},
            {"user_id": "u002", "email": "bob.new@example.com"},
        ])
        assert sum(1 for r in update_result if r["status"] == "updated") == 2
        # 清理孤儿账号
        cleanup_result = service.cleanup_orphan_accounts(["u001", "u002"])
        assert cleanup_result["orphan_count"] >= 1, "应至少清理 1 个孤儿账号"
        # 验证孤儿账号已被禁用
        assert repo.users["u003"].enabled is False, "u003 应已被禁用"
        print(f"  ✓ 批量更新成功，孤儿账号清理 {cleanup_result['orphan_count']} 个")

        # ---------- 全部通过 ----------
        print("\n" + "=" * 60)
        print("自检全部通过 ✔  (selftest OK)")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n❌ 自检失败: {e}")
        return 1
    except M365Error as e:
        print(f"\n❌ 自检遇到 M365 错误: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ 自检遇到未预期异常: {type(e).__name__}: {e}")
        return 1


# ============================================================
# 命令行入口
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="world-of-m365",
        description="M365 运维脚本化工具集（独立实现）",
        epilog="示例: python main.py --selftest",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="运行内置自检（使用硬编码样例数据，离线验证核心逻辑）",
    )
    parser.add_argument(
        "--action",
        choices=[
            "create-mailbox",
            "create-team",
            "add-team-members",
            "adjust-permissions",
            "query-logs",
            "cleanup-orphans",
        ],
        help="指定要执行的操作",
    )
    parser.add_argument("--data", help="JSON 格式的操作数据（与 --action 配合使用）")
    parser.add_argument("--verbose", action="store_true", help="输出详细信息")
    return parser


def handle_action(args: argparse.Namespace) -> int:
    """处理具体的业务操作（演示用，实际使用需连接真实 M365）。"""
    repo = M365Repository()
    service = M365Service(repo)

    if not args.action:
        print("未指定操作。使用 --selftest 运行自检，或 --action 指定操作。")
        return 1

    if not args.data:
        print("错误: 缺少 --data 参数（JSON 格式）")
        return 1

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError:
        print("错误: --data 不是合法的 JSON 格式")
        return 1

    try:
        if args.action == "create-mailbox":
            result = service.batch_create_mailbox(data if isinstance(data, list) else [data])
        elif args.action == "create-team":
            result = service.batch_create_teams(data if isinstance(data, list) else [data])
        elif args.action == "add-team-members":
            team_id = data.get("team_id", "")
            user_ids = data.get("user_ids", [])
            result = service.add_team_members_batch(team_id, user_ids)
        elif args.action == "adjust-permissions":
            site_id = data.get("site_id", "")
            permissions = data.get("permissions", {})
            result = service.batch_adjust_permissions(site_id, permissions)
        elif args.action == "query-logs":
            result = service.query_login_logs(
                user_id=data.get("user_id"), days=data.get("days", 30)
            )
        elif args.action == "cleanup-orphans":
            active = data.get("active_user_ids", [])
            result = service.cleanup_orphan_accounts(active)
        else:
            raise M365Error("E001", f"不支持的操作: {args.action}")

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    except M365Error as e:
        print(f"操作失败: {e}")
        return 1
    except Exception as e:
        print(f"未预期错误: {type(e).__name__}: {e}")
        return 1


def main() -> int:
    """主入口函数。"""
    parser = build_parser()
    args = parser.parse_args()

    # 自检模式
    if args.selftest:
        return run_selftest()

    # 无参数时显示帮助
    if not args.action:
        parser.print_help()
        return 0

    # 业务操作模式
    return handle_action(args)


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
