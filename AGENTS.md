# AGENTS.md — quant-harness 开发指南

quant-harness 是一个 Python 编写的事件驱动量化研究框架：既能做单标的快速回测（`engine`），也能以**同一套代码路径**驱动 A 股日线组合模拟盘（`daily` + `paper`）。当前不连接券商账户、不发送真实委托，`daily` 是本地**模拟盘**而非实盘交易系统。模拟盘验证的是**过程严谨性**（无未来函数、真实费用与规则、风控约束、如实报告），不承诺任何收益。

本文件面向后续参与迭代的工程师与 AI 编码代理，描述模块职责、不可破坏的不变量、以及迭代规范。

---

## 1. 常用命令

```bash
pip install -e ".[dev]"          # 安装（开发模式，含 pytest）
pip install -e ".[live]"         # 安装 akshare（仅实盘/取数需要）
pytest                           # 全量测试（离线，不依赖网络与 akshare）
python examples/run_sma_cross.py # 单标的 SMA 交叉示例回测

quant-harness daily [--resume]   # 今日模拟盘周期（幂等；--resume 清除熔断标志）
quant-harness replay --start YYYY-MM-DD --end YYYY-MM-DD [--refresh]  # 历史走查回测
quant-harness status             # 查看账户状态
quant-harness report [YYYY-MM-DD]# 查看日报
```

- 入口：`quant_harness/cli.py`（`pyproject.toml` 中的 console script）。
- 配置：默认读取 `./config.toml`，可用 `--config` 覆盖；相对路径按配置文件所在目录解析（见 `config.py:load_config`）。
- 运行产物均被 `.gitignore` 排除：`state/`（账户状态）、`reports/`（日报）、`data/cache/`（行情快照）、`logs/`。

---

## 2. 架构总览

```
src/quant_harness/
├── cli.py                 # CLI 入口：daily / replay / status / report
├── config.py              # TOML → frozen dataclass 配置（Fees/RiskConfig/StrategyConfig/Config）
├── reporting.py           # Markdown 日报 + 终端状态输出（含免责声明）
├── data/                  # ── 数据层 ──
│   ├── types.py           # Bar（frozen dataclass，OHLCV）
│   ├── loader.py          # CSV 加载 + 合成随机游走数据（测试/示例用）
│   ├── calendar.py        # 交易日历（参考标的即日历）+ slice_history 无未来边界
│   └── akshare_source.py  # akshare 日线数据源，全量快照缓存
├── strategy/              # ── 策略接口 ──
│   ├── base.py            # Strategy（单标的 bar-by-bar，配合 engine）
│   └── portfolio.py       # PortfolioStrategy（组合级，配合 daily runner）
├── strategies/            # ── 策略实现 ──
│   ├── sma_cross.py       # SMA 交叉（单标的，走 engine）
│   └── momentum_rotation.py # 动量轮动 top-k + 滞回（组合级，走 daily）
├── engine/                # ── 单标的回测引擎（v0.1 内核）──
│   ├── broker.py          # SimBroker：挂单 t → t+1 开盘成交
│   ├── backtest.py        # run_backtest 主循环
│   └── metrics.py         # 收益/年化/夏普/最大回撤/胜率/盈亏比
├── paper/                 # ── A 股模拟盘账户层 ──
│   ├── account.py         # PaperAccount：A股规则撮合 + JSON 原子持久化
│   ├── orders.py          # reconcile：目标权重 → 具体订单
│   ├── risk.py            # RiskManager：止损/权重上限/敞口上限/回撤熔断
│   └── stats.py           # trade_stats：交易级统计
└── daily/
    └── runner.py          # ── 日线运行器：run_day / run_daily / run_replay ──
```

两套执行栈**有意分离**，勿混用：

| 栈 | 策略接口 | 撮合 | 场景 |
|---|---|---|---|
| `engine`（单标的） | `strategy.base.Strategy`（`on_bar`） | `engine.broker.SimBroker` | 快速研究、示例、合成数据 |
| `daily`（组合日线） | `strategy.portfolio.PortfolioStrategy`（`target_weights`） | `paper.account.PaperAccount` | A 股模拟盘 live + replay |

---

## 3. 模块详解

### 3.1 数据层 `data/`

- **`Bar`**（`types.py`）：全库唯一的价格单元，frozen dataclass。日期语义为 `timestamp.date()`。
- **`AkshareDataSource`**（`akshare_source.py`）：
  - `refresh(symbol, end_date)`：akshare `stock_zh_a_hist` 拉取**前复权（qfq）**日线，失败按 `fetch_retries` / `fetch_retry_sleep_s` 重试。
  - 缓存是**全量快照重写**，绝不追加——前复权价格在公司行为（分红送转）后会整体重定基，追加会把不同复权基准拼接在一起。
  - `akshare` 为**延迟导入**：核心包、单元测试、replay（离线缓存）一律不触网。
- **`calendar.py`**：交易日历不依赖外部 API——**参考标的（`reference_symbol`）的 bar 序列就是日历**。
  - `slice_history(history, as_of)` 是策略看到历史的**唯一入口**（no-lookahead 边界），除此之外的代码才允许持有全量序列。
- **`loader.py`**：CSV 格式 `timestamp,open,high,low,close,volume`（即缓存格式）；`generate_synthetic_bars` 生成确定性随机游走，供测试与示例。

### 3.2 策略层 `strategy/` 与 `strategies/`

- `strategy/` 放**接口**，`strategies/` 放**实现**。
- **`PortfolioStrategy`**（组合级，主力接口）：
  - 契约：`target_weights(history, account, as_of) -> dict[symbol, weight]`，是**(切片后)历史 + 当前账户状态**的纯函数——**不持有跨日内部状态**。这样 live 与 replay 天然一致，无需序列化策略状态。
  - 可选提供 `rank(history, as_of)`（如 `MomentumRotation`），供日报展示排序信息。
- **`Strategy`**（单标的接口）：`on_bar(bar, broker)` + `on_start/on_finish`，仅服务于 `engine` 栈。
- **`MomentumRotation`**：`close/close[-1-window] - 1` 动量排名，持有 top_k；持有标的享有 `rank_buffer` 滞回（跌出 top_k+buffer 才清仓），抑制边界换手。

### 3.3 模拟盘账户层 `paper/`

- **`PaperAccount`**（`account.py`）——多标的、只做多的**本地模拟账户**，实现 A 股市场规则；它不会连接券商或发出真实委托：
  - **整手**：买入按 100 股取整（卖出允许清仓时的零股）。
  - **T+1**：当日买入不可当日卖出（次开盘成交机制下天然满足，保留作纵深防御）。
  - **主板涨跌停**：开盘价触及前收盘 ±10%（容差 0.095）即拒单，理由分别为 `price_limit_up` / `price_limit_down`。
  - **费用**：佣金万 2.5、单笔最低 5 元、卖出印花税 0.05%、滑点 0.1%。
  - **每一笔拒单都有理由**（`CancelledOrder`：`suspended` / `price_limit_up` / `price_limit_down` / `insufficient_cash` / `no_position` / `t1_restriction`），**绝不静默丢弃订单**。
  - 持久化到 `state/account.json`，**原子写**（写 `.tmp` 后 `os.replace`）。
- **`orders.py:reconcile`**：目标权重 → 订单。先卖后买（释放现金与持仓位）；买入按目标市值减去现持仓的差额、向下取整手、并预检现金；同一批次同一标的不可能既买又卖。
- **`risk.py:RiskManager`**（四道闸，按日事件序生效）：
  1. `max_symbol_weight`：单票市值/净值上限（默认 25%）。
  2. `max_total_exposure`：总持仓市值/净值上限（默认 80%）。
  3. `stop_loss`：收盘价 ≤ 成本 ×(1−8%) → 次日全仓止损。
  4. `drawdown_halt`：回撤破 10% → **熔断**：清仓所有持仓 + 暂停交易，必须人工 `--resume` 才恢复。
  - `filter_orders` 对买单做上限裁剪（整手向下），卖单无条件放行。
- **`stats.py:trade_stats`**：基于卖出成交的已实现盈亏统计胜率与盈亏比。

### 3.4 日线运行器 `daily/runner.py`（系统心脏）

`run_day` 定义了**每日事件序列**，live 与 replay 完全共用：

```
对每个交易日 D：
1. D-1 收盘入队的订单在 D 开盘成交（滑点、拒单记录）
2. 按 D 收盘价盯市
3. 记录净值点，更新峰值
4. 风控：回撤熔断（清仓+停机）或止损退出
5. 策略：target_weights(slice_history(history, D)) —— 策略永远看不到 D 之后的数据
   → reconcile 成订单 → 剔除当日强制退出标的的买单 → 风控过滤 → 入队待 D+1
6. （live）持久化状态 + 写日报
```

- **`run_daily`**：实盘模拟。以 `account.last_processed_date` 为游标，**日期驱动 + 幂等**——漏跑的交易日会自动用历史价格补跑，重跑同日是 no-op。数据未发布/非交易日返回 0（退出码）；熔断返回 2。
- **`run_replay`**：walk-forward 回测，内存中重放同一 `run_day` 循环，输出指标与权益曲线。

### 3.5 引擎 `engine/`（v0.1 单标的内核）

`run_backtest(bars, strategy)`：bar 驱动，t 时刻下单 → t+1 开盘成交（与 daily 栈同一防未来原则）。`metrics.py:compute_metrics` 同时服务两个栈（daily replay 亦复用）。这是研究快验工具，**新功能默认落在 `daily`/`paper` 栈**。

### 3.6 配置 `config.py` 与 `config.toml`

- 全部 frozen dataclass；TOML 缺省键回落到 dataclass 默认值——**新增配置项必须给默认值**，保证旧配置文件向后兼容。
- `config.toml` 中的注释（股票池只选主板大盘股避开 20% 涨跌停板块等）承载了**风控假设**，修改前先理解。

---

## 4. 不可破坏的不变量（Invariants）

任何改动必须维持以下性质，`tests/test_runner_replay.py` 是其端到端证明：

1. **无未来函数（no lookahead）**
   - t 收盘的决策只能依赖 ≤t 的数据：策略一律通过 `slice_history` 取数。
   - t 收盘的订单只能在 t+1 开盘成交（含滑点），绝不在信号日收盘成交。
   - 修改 D 之后的历史数据不得改变 ≤D 的任何决策（有专门测试）。
2. **live = replay**：`run_daily` 与 `run_replay` 必须共用 `run_day`，任何撮合/风控/费用逻辑只写一处。
3. **幂等与补跑**：`run_daily` 以 `last_processed_date` 为界；重复运行是 no-op，中断后补跑结果与一次跑完一致。
4. **账户不变量**：现金 ≥ 0（容差 1e-9）、净值恒正、买入数量恒为整手（清仓卖除外）、持仓不可为负。
5. **无静默失败**：每笔被拒订单记录理由；数据源失败有重试且最终抛错（带原因），缓存回退要打印 warning。
6. **A股规则完整性**：整手 / T+1 / 主板涨跌停 / 最低佣金 / 卖出印花税，缺一不可。
7. **熔断语义**：熔断后只清仓、不再开新仓；恢复必须人工 `--resume`。
8. **持久化安全**：`account.json` 与行情缓存一律临时文件 + 原子替换，任何时刻崩溃不留半写状态。
9. **离线可测**：核心逻辑与测试不依赖 akshare / 网络（akshare 延迟导入；测试用 Stub 数据源）。
10. **免责声明**：日报必须附 `reporting.py:DISCLAIMER`（模拟盘不构成投资建议）。

---

## 5. 迭代规范

### 5.1 新增组合策略

1. 在 `strategies/` 新建模块，继承 `PortfolioStrategy`，实现 `target_weights`。
2. **禁止跨日内部状态**：策略是 `(切片历史, 账户状态) → 权重` 的纯函数。若确需状态，必须设计成可从历史重算（无状态化）或显式定义序列化方案并同步 `account.json` schema——优先前者。
3. 权重语义：目标权重按 `equity` 计，`reconcile` 会处理差额与整手；不在策略里自行算股数。
4. 参数通过 `StrategyConfig` + TOML 暴露，带默认值；在 `_build_strategy`（`daily/runner.py`）接线。策略选择若需可配置，改动点集中在 `_build_strategy`，勿在 `run_day` 里做分支。
5. 测试：参照 `tests/test_momentum_rotation.py` 的数据构造方式（`series()`/`weekdays()` 辅助函数在 `tests/test_runner_replay.py`，可复用或提炼），至少覆盖 warmup、入选、退出/滞回。

### 5.2 新增数据源 / 修改数据层

- 对齐 `AkshareDataSource` 的契约：`refresh(symbol, end_date) -> list[Bar]`（全量快照、失败重试后抛 `RuntimeError`）与 `load_cached(symbol) -> list[Bar]`。runner 通过构造函数替换注入，测试用 `monkeypatch` 替换（见 `TestRunDailyIdempotency`）。
- **缓存永远全量重写，不追加**（前复权重定基风险）。
- 新列/新字段进 `Bar` 必须给默认值，避免破坏既有构造点。
- 涉及新依赖：只加进 `[project.optional-dependencies]`，并保持延迟导入，核心包零网络依赖。

### 5.3 修改风控

- 风控是**安全机制，不是调参对象**：放宽上限或移除闸门需要极强理由，并在 PR 描述中论证。
- 新风控规则实现为 `RiskManager` 的方法并保持"生成订单/过滤订单"的纯函数风格；生效顺序遵循 `run_day` 事件序（熔断 > 止损 > 策略）。
- 每条规则配单元测试（参照 `tests/test_risk.py` 的触发/不触发/停牌三态）。

### 5.4 配置与状态演进

- `config.toml` / `Config`：只增不改语义；新键必有 dataclass 默认值；已发布键改名需保留旧键兼容或提供迁移说明。
- `state/account.json` schema：**只加字段不删改**（load 端用 `payload.get(key, default)` 兼容旧状态文件）；涉及结构性变更时在 `PaperAccount.load/save` 内做版本兼容，并补充"旧状态文件可加载"的测试。
- 版本号：`pyproject.toml` 与 `quant_harness/__init__.py:__version__` 需同步更新（当前存在 0.2.0 / 0.1.0 不同步的遗留，下次发版一并修正）。

### 5.5 CLI 扩展

- 新命令在 `cli.py` 注册子命令，业务实现落在对应模块，CLI 只做参数解析与输出。
- 退出码约定：`0` 正常/幂等 no-op；`1` 用户错误（无状态文件、无报告等）；`2` 熔断需人工介入。新命令遵守该约定。
- 输出信息保持"如实、可 grep"风格：日报/状态里的数字均可与 `account.json` 对账。

### 5.6 测试要求

- 框架：pytest，`tests/` 下按 `test_<模块>.py` 组织，测试类按主题分组（如 `TestNoLookahead` / `TestHaltAndStopLoss`）。
- **全部离线**：不 mock 网络、不打真接口，用合成 bar（`series()`）与 Stub 数据源。
- 回归纪律：修 bug 先写失败测试再修；涉及事件序、成交价、幂等的改动必须过 `test_runner_replay.py` 全量。
- 新增撮合/风控行为时，至少断言：成交价（含滑点）、成交日、拒单理由、现金与净值不变量。

### 5.7 代码风格

- Python ≥ 3.10；模块头 `from __future__ import annotations`；全量类型标注。
- 数据载体用 `@dataclass`（可变的领域对象）/ `@dataclass(frozen=True)`（值对象如 `Bar`、配置）。
- 公共行为写 docstring，说明**契约与边界条件**（何时拒单、何时重试、边界含否），参照 `paper/account.py`、`daily/runner.py` 的注释密度。
- 领域常量（如 `MAIN_BOARD_LIMIT = 0.095`）就地命名并注释取值原因。
- 货币与比例用 `float`，比较用容差（`<= 0`、`1e-9`）；日期一律 `datetime.date`，序列化为 ISO 格式。
- 报告与用户可见文案为中文；代码标识符与注释为英文。

### 5.8 迭代路线建议（Roadmap 方向）

以下为可选方向，实施时仍须遵守第 4 节不变量：

- **多策略选择**：`StrategyConfig` 增加 `name` 字段，`_build_strategy` 按名注册分发（注册表 dict，勿写 if-elif 链）。
- **基准对比**：replay 输出相对参考标的（`reference_symbol`）的超额收益与 beta。
- **指标扩展**：`engine/metrics.py` 增加_calmar_、回撤持续期等；注意 `compute_metrics` 同时服务两个栈，保持纯函数。
- **数据源抽象**：若引入第二数据源，提炼 `DataSource` Protocol（refresh/load_cached），`AkshareDataSource` 作为首个实现。
- **复盘工具**：基于 `state/account.json` 的 trades/cancelled 做聚合分析命令（如 `quant-harness analyze`）。
- **ETF/指数支持**：需重新评估涨跌停规则（ETF 无 10% 限制），价格限制逻辑须按标的类型参数化而非写死主板规则。

---

## 6. 已知边界与注意事项

- **只支持主板 10% 涨跌停假设**：股票池刻意排除 300xxx/688xxx（20% 板）；且股票价格需 < ¥500 使整手能落入仓位上限。引入新品种前先泛化 `MAIN_BOARD_LIMIT` 逻辑。
- **停牌处理**：停牌标的订单取消（`suspended`）；盯市保留最后已知价；止损在停牌期间不评估、复牌后重估。
- **qfq 缓存与回测一致性**：历史 bar 是前复权价，分红日的"价格跳变"已被抹平，回测里的止损/涨跌停判断基于复权价而非真实挂牌价——这是有意的简化，如需精确涨跌停判断需引入不复权序列。
- **末日订单**：最后一天入队的订单永远不会成交（无下一开盘），replay 结束时留在 `pending` 中——属预期行为，勿"顺手补成交"。
- **akshare 接口稳定性**：`stock_zh_a_hist` 返回中文列名（`日期/开盘/...`），`_to_bars` 的解析与上游强耦合，升级 akshare 后应跑一次真实取数冒烟。
