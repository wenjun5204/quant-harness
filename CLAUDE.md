# quant-harness — A股每日模拟盘系统

事件驱动回测框架 + A股日线模拟盘（paper trading）。**当前为模拟盘阶段，不实盘下单。**

## 关键事实

- 默认策略：等权持有 29 只主板蓝筹 + 池 120 日趋势过滤（趋势 ≤0 空仓，>0 持有）
- 8 年回测结论（写死在 README）：无稳定 alpha，系统的价值是风控与纪律，不是预测
- cron 已安装（工作日 17:05 / 21:05 自动 `quant-harness daily`），幂等、漏跑自动补
- 所有命令用 `./.venv/bin/quant-harness`（venv 已装好）

## 常用命令

```bash
./.venv/bin/quant-harness status          # 净值/持仓/待成交
./.venv/bin/quant-harness report          # 最新日报（含趋势读数）
./.venv/bin/quant-harness scan            # 全池近况扫描（多窗口动量/波动/回撤）
./.venv/bin/quant-harness daily           # 手动补跑（幂等，cron 漏了就跑这个）
./.venv/bin/quant-harness daily --resume  # 熔断后手动恢复（先向用户确认）
./.venv/bin/quant-harness replay --start 2026-01-02 --end 2026-09-02  # 回放
./.venv/bin/python -m pytest              # 测试（96 个，离线）
```

## Agent 日常值守规则

用户问"今天怎么样/该做什么"时，按 `/quant` 技能的流程汇报。核心判断：

1. **趋势读数 ≤ 0 且空仓** → 正常等待，告知用户无需操作
2. **「明日待成交」有订单** → 这是用户实盘手动跟单的信号，明确列出标的/方向/数量
3. **⛔ halted** → 熔断，需用户决定是否 `--resume`；不要自动恢复
4. **数据拉取失败** → 次日自动补；连续失败才需要排查（`logs/daily.log`）

## 不可破坏的不变量

- 信号日收盘出单、次日开盘成交（防未来函数），有测试锁定，勿改
- 改策略参数前必须先 `sweep` 多窗口回测对比基准，样本内最优 ≠ 可用
- 禁止向用户承诺盈利；汇报里如实给出回测与模拟盘的差距
