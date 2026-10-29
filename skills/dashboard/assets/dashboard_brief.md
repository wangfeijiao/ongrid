# 看板简报（dashboard_brief）

```json
{
  "metrics": "报名数、参与率、满意度、成本",
  "refresh": "static",
  "source": "CSV 导出",
  "language": "zh-CN"
}
```

字段说明：
- `metrics`：指标清单（中性示例，如报名数/参与率）。
- `refresh`：static（静态）/ refresh（定时）/ realtime（实时）。
- `source`：数据源描述（CSV/API/数据库）。
- `language`：zh-CN / zh-TW / en / ja。

运行：
```
python scripts/generate_board.py --brief dashboard_brief.json --all
```
