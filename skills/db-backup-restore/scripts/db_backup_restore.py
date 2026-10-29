# -*- coding: utf-8 -*-
"""数据库备份/恢复独立运维脚本（v1.0.0）

不依赖后端包，直接对 data/ 下的 SQLite 按年分库做 WAL 安全的整库复制与恢复。
用法（在 程序源代码/ 目录下运行）：
  python scripts/db_backup_restore.py list
  python scripts/db_backup_restore.py backup            # 备份到 data/backup/YYYY-MM-DD/
  python scripts/db_backup_restore.py restore 2026-09-29   # 从该日备份恢复（先另存 rollback 快照）
  python scripts/db_backup_restore.py restore rollback_20260930_120000  # 退回恢复前的快照

特点：恢复前自动另存 rollback 快照、校验完整性、绝不半途覆盖（与 backend/backup.py 行为一致）。
"""
import os, re, shutil, sqlite3, sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 程序源代码/
DATA_DIR = os.path.join(BASE, "data")
BACKUP_ROOT = os.path.join(DATA_DIR, "backup")
YEARS = [None, 2024, 2025]  # None=当年库(目标系统.db)；按实际年份库扩展
KEEP_DAYS = 7


def db_path(year):
    if year is None:
        return os.path.join(DATA_DIR, "目标系统.db")
    return os.path.join(DATA_DIR, "目标系统_%s.db" % year)


def _db_copy(src, dst):
    if not os.path.exists(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        s = sqlite3.connect(src, timeout=10)
        d = sqlite3.connect(dst, timeout=10)
        try:
            with d:
                s.backup(d)
        finally:
            d.close(); s.close()
    except Exception:
        try:
            c = sqlite3.connect(src, timeout=10)
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()
        except Exception:
            pass
        shutil.copy2(src, dst)
    for ext in ('-wal', '-shm'):
        try:
            if os.path.exists(dst + ext):
                os.remove(dst + ext)
        except OSError:
            pass
    return True


def list_backups():
    if not os.path.isdir(BACKUP_ROOT):
        print('无备份目录'); return
    for name in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        p = os.path.join(BACKUP_ROOT, name)
        if not os.path.isdir(p):
            continue
        files = os.listdir(p)
        size = sum(os.path.getsize(os.path.join(p, f)) for f in files if os.path.isfile(os.path.join(p, f)))
        print('  %-26s %5.1f MB  %s' % (name, size / 1048576.0, ', '.join(files)))


def run_backup(force=True):
    day = datetime.now().strftime('%Y-%m-%d')
    d = os.path.join(BACKUP_ROOT, day)
    os.makedirs(d, exist_ok=True)
    done = []
    for y in YEARS:
        src = db_path(y)
        if not os.path.exists(src):
            continue
        _db_copy(src, os.path.join(d, os.path.basename(src)))
        done.append(os.path.basename(src))
    # 清理旧日常备份
    names = [x for x in os.listdir(BACKUP_ROOT) if re.match(r'^\d{4}-\d{2}-\d{2}$', x) and os.path.isdir(os.path.join(BACKUP_ROOT, x))]
    for old in sorted(names)[:-KEEP_DAYS]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
    # 清理旧 rollback 快照（保留 5 份）
    rbs = sorted([x for x in os.listdir(BACKUP_ROOT) if x.startswith('rollback_') and os.path.isdir(os.path.join(BACKUP_ROOT, x))])
    for old in rbs[:-5]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
    print('已备份到 %s ：%s' % (d, ', '.join(done) or '（无可用库）'))
    return d


def restore_from(day):
    d = day if os.path.isdir(day) else os.path.join(BACKUP_ROOT, day)
    if not os.path.isdir(d):
        print('备份不存在：%s' % day); sys.exit(1)
    need = [os.path.basename(db_path(y)) for y in YEARS]
    miss = [f for f in need if not os.path.exists(os.path.join(d, f))]
    if miss:
        print('备份不完整，缺少 %s，已取消恢复' % ', '.join(miss)); sys.exit(1)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    snap = os.path.join(BACKUP_ROOT, 'rollback_%s' % ts)
    for y in YEARS:
        src = db_path(y)
        if os.path.exists(src):
            _db_copy(src, os.path.join(snap, os.path.basename(src)))
    for y in YEARS:
        f = os.path.basename(db_path(y))
        _db_copy(os.path.join(d, f), db_path(y))
    print('已从 %s 恢复；恢复前数据已另存为 rollback_%s，建议重启程序生效。' % (os.path.basename(d), ts))


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    cmd = sys.argv[1]
    if cmd == 'list':
        list_backups()
    elif cmd == 'backup':
        run_backup()
    elif cmd == 'restore':
        if len(sys.argv) < 3:
            print('用法: db_backup_restore.py restore <YYYY-MM-DD|rollback_...>'); sys.exit(2)
        restore_from(sys.argv[2])
    else:
        print('未知命令: %s' % cmd); sys.exit(2)


if __name__ == '__main__':
    main()
