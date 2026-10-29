# -*- coding: utf-8 -*-
"""性能三件套一键运行 + 汇总（通用版，已脱敏）。

运行前需后端已在 <API_BASE> 监听（默认 http://127.0.0.1:8099）。
三个单项脚本与本文件同在 scripts/ 目录下，自动按相对路径调用。

用法：
  python scripts/perf_suite.py
  python scripts/perf_suite.py --only speed
"""
import os, sys, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))  # 本脚本所在 scripts/ 目录


def run(name, fname):
    p = os.path.join(HERE, fname)
    if not os.path.exists(p):
        print('  [跳过] 找不到 %s' % fname)
        return
    print('\n===== %s =====' % name)
    try:
        subprocess.run([sys.executable, p], cwd=HERE, check=False)
    except Exception as e:
        print('  [异常] %s: %s' % (name, e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='all', choices=['all', 'speed', 'edge', 'concurrency'])
    args = ap.parse_args()

    if args.only in ('all', 'speed'):
        run('speed_test (API 延迟)', 'speed_test.py')
    if args.only in ('all', 'edge'):
        run('edge_perf_test (边界/基线)', 'edge_perf_test.py')
    if args.only in ('all', 'concurrency'):
        run('concurrency_test (并发写)', 'concurrency_test.py')

    print('\n== 判读提示 ==')
    print('  speed: 本地回环正常应 < 50ms/端点；某端点异常高 → 查其 SQL/聚合')
    print('  edge:  异常应被正确处理（不 500、不泄漏连接）')
    print('  concurrency: 应全部成功、无重复、失败后服务仍 200')
    print('  ⚠ 跑完清理 _KL_/_CONC_ 前缀测试数据，或还原空库重导，避免污染测试库')


if __name__ == '__main__':
    main()
