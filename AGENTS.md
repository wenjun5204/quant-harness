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
quant-harness sweep --window 2023-01-01:2025-12-31:train --window 2026-01-01:2026-06-30:oos \
                    [--set strategy.momentum_window=20,40,60] [--benchmark]  # 多窗口×参数网格扫描
quant-harness status             # 查看账户状态
quant-harness report [YYYY-MM-DD]# 查看日报
```

- `sweep` 的 `--window` 可重复传入多个窗口（选参窗口与样本外窗口分开传入），`--set` 指定参数路径与候选值（可重复），`--benchmark` 附等权买入持有对照行——使用规则见第 7 节研究纪律。

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
│   ├── momentum_rotation.py # 动量轮动 top-k + 滞回 + 绝对动量下限（组合级）
│   └── buy_and_hold.py    # 等权买入持有基准（一切主动策略的对照尺）
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
    ├── runner.py          # ── 日线运行器：run_day / run_daily / run_replay ──
    └── sweep.py           # 参数扫描：一次载数 × 多窗口 × 参数网格（选参/验证分离）
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
- **`MomentumRotation`**：`close/close[-1-window] - 1` 动量排名，持有 top_k；持有标的享有 `rank_buffer` 滞回（跌出 top_k+buffer 才清仓），抑制边界换手；`min_momentum` 绝对动量下限——全部标的动量为负时持有现金。
- **`BuyAndHold`**：股票池等权买入持有基准。任何主动策略的结论都必须相对它表述（跑赢/持平/跑输），见第 7 节研究纪律。

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
- **`daily/sweep.py:run_sweep`**：一次加载历史，跨多个时间窗口 × 参数网格运行 replay。窗口独立评估——选参窗口与样本外验证窗口必须分开。
- **取数限速与发布偏差防护**：`FETCH_SYMBOL_DELAY_S` 控制逐标的取数间隔（对 akshare 上游限速的礼貌约束）；`_refetch_laggards` 在参考标的有今日 bar 而部分标的没有时重取一次，避免把"数据晚发布"误判为停牌（被误判停牌的标的当天不会收到买单）。

### 3.5 引擎 `engine/`（v0.1 单标的内核）

`run_backtest(bars, strategy)`：bar 驱动，t 时刻下单 → t+1 开盘成交（与 daily 栈同一防未来原则）。`metrics.py:compute_metrics` 同时服务两个栈（daily replay 亦复用）。这是研究快验工具，**新功能默认落在 `daily`/`paper` 栈**。

### 3.6 配置 `config.py` 与 `config.toml`

- 全部 frozen dataclass；TOML 缺省键回落到 dataclass 默认值——**新增配置项必须给默认值**，保证旧配置文件向后兼容。
- `config.toml` 中的注释（股票池只选主板大盘股避开 20% 涨跌停板块、策略默认参数的选参/验证依据等）承载了**风控与研究假设**，修改前先理解；策略默认参数的注释范式见 7.1。
- `[data]` 下的 `fetch_retries` / `fetch_retry_sleep_s` / `fetch_lookback_years` 属于对上游 akshare 的取数约束，受 7.2 规则保护（只能收紧不能放宽）。

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
11. **目标权重契约**：`PortfolioStrategy.target_weights` 只能返回股票池内标的的有限非负权重；非杠杆账户的权重和必须 ≤ 1。策略不得依赖 `RiskManager` 以订单裁剪的副作用修正超额目标权重。
12. **信息可得性无未来函数**：除行情日期边界外，任何外部数据还必须受实际可得时间约束；策略在 D 的决策只能使用 D 时点已公开的数据。

---

## 5. 迭代规范

### 5.1 新增组合策略

1. 在 `strategies/` 新建模块，继承 `PortfolioStrategy`，实现 `target_weights`。
2. **禁止跨日内部状态**：策略是 `(切片历史, 账户状态) → 权重` 的纯函数。若确需状态，必须设计成可从历史重算（无状态化）或显式定义序列化方案并同步 `account.json` schema——优先前者。
3. 权重语义：目标权重按 `equity` 计，`reconcile` 会处理差额与整手；不在策略里自行算股数。返回结果只能包含股票池内标的，权重必须有限且非负，非杠杆账户的权重和必须 ≤ 1。
4. `top_k` 语义必须在策略 docstring 与测试中固定为“最多持有 k 个标的”或“最多新开 k 个标的、滞回期间允许额外保留”。若滞回导致额外保留标的，策略必须显式定义再归一化或分配规则；禁止依赖订单顺序和风控裁剪决定最终持仓。
5. 参数通过 `StrategyConfig` + TOML 暴露，带默认值；在 `_build_strategy`（`daily/runner.py`）接线。策略选择若需可配置，改动点集中在 `_build_strategy`，勿在 `run_day` 里做分支。
6. 测试：参照 `tests/test_momentum_rotation.py` 的数据构造方式（`series()`/`weekdays()` 辅助函数在 `tests/test_runner_replay.py`，可复用或提炼），至少覆盖 warmup、入选、退出/滞回、权重总和与超额持仓的分配规则。

### 5.2 新增数据源 / 修改数据层

- 对齐 `AkshareDataSource` 的契约：`refresh(symbol, end_date) -> list[Bar]`（全量快照、失败重试后抛 `RuntimeError`）与 `load_cached(symbol) -> list[Bar]`。runner 通过构造函数替换注入，测试用 `monkeypatch` 替换（见 `TestRunDailyIdempotency`）。
- **缓存永远全量重写，不追加**（前复权重定基风险）。
- 新列/新字段进 `Bar` 必须给默认值，避免破坏既有构造点。
- 新增非行情数据（财务、公告、指数成分、行业分类、基本面因子等）必须携带或可推导 `published_at` / `effective_at`；策略只能读取决策时点已发布且已生效的数据。盘后修订、补发与复权重算的数据不得回写成历史上"当时已知"的信息。
- 涉及新依赖：只加进 `[project.optional-dependencies]`，并保持延迟导入，核心包零网络依赖。

### 5.3 修改风控

- 风控是**安全机制，不是调参对象**：放宽上限或移除闸门需要极强理由，并在 PR 描述中论证；不得为提高收益、夏普或胜率而选择风控参数。
- 风控阈值变更必须附安全论证和压力测试：至少覆盖跳空、连续跌停、停牌、涨跌停无法卖出、数据缺失和极端滑点；不得仅以 sweep 的收益指标作为变更依据。
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

- **多策略选择**：`StrategyConfig` 增加 `name` 字段，`_build_strategy` 按名注册分发（注册表 dict，勿写 if-elif 链）。目前已有两个以上策略实现（含 `BuyAndHold`），`_build_strategy` 硬编码 `MomentumRotation` 的现状应尽快消除。
- ~~**基准对比**~~：已由 `BuyAndHold` + `run_sweep(benchmark=True)` 落地；后续可增加相对参考标的的超额收益与 beta。
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

---

## 7. 研究纪律（sweep 与参数选择）

工程已具备参数扫描（`daily/sweep.py`）与基准（`BuyAndHold`）能力，实验工具必须配实验纪律。本节规则约束一切"调参数、选策略、下结论"的迭代，目的是让 harness 朝实用有效而非样本内好看的方向演进。

### 7.1 防过拟合（硬性规则）

1. **选参/验证分离**：任何写入 `config.toml` 默认值的**策略参数**，必须能追溯到 sweep 的"选择窗口 + 样本外窗口"证据（`momentum_window = 60` 的注释是标准范式：注明选择窗口、样本外窗口、与基准的对比结论）。调参 PR 必须附此证据；风控参数遵循 5.3 的安全与压力测试要求，不得按收益指标选取。
2. **禁止挑最优单元格**：结论不允许引用"全网格最优组合在它的最优窗口"的表现——这是 sweep 在骗人。只允许引用：样本外窗口结果，或全窗口最差值（worst-window）。
3. **网格规模限制**：参数网格组合数 × 窗口数原则上 ≤ 200 次 replay；超出需在 PR 中论证为什么不可缩减（大规模扫参等于变相多重比较）。
4. **基准对比强制**：策略改动的 PR 必须附等权 `BuyAndHold` 对照（`run_sweep(benchmark=True)`），并明确写出三态结论之一：跑赢 / 持平 / 跑输。回避比较的结论无效。

### 7.2 数据纪律

5. **回测结论绑定数据快照**：sweep/replay 的结论必须注明缓存数据的时间范围（各标的最后 bar 日期即可）。qfq 复权价会随公司行为重定基，不同日期取的缓存不是同一组数，跨快照比较结论无效。
6. **股票池变更即新实验**：`symbols` 列表任何增删都视为新策略版本，既往 sweep 结论作废，须重新走选参/验证流程。禁止"边跑模拟盘边换池子"——池子变更后应以新账户文件重开，或明确声明旧账户数据不可比。
7. **取数限速参数是契约**：`FETCH_SYMBOL_DELAY_S`、`fetch_retries`、`fetch_retry_sleep_s`、`fetch_lookback_years` 是对上游 akshare 的礼貌与稳定性约束，只能收紧不能放宽。
8. **信息发布时间约束**：新增财务、公告、指数成分、行业分类、基本面等数据时，必须按 `published_at` / `effective_at` 进行切片；仅按业务日期截取不构成无未来函数。新增数据源必须测试：修改决策日之后才发布的信息，不得改变该日的信号或订单。
9. **股票池与幸存者偏差**：当前静态 `symbols` 只可表述为"指定名单的历史模拟"，不得泛化为市场整体或指数成分表现。若策略使用指数成分或市场范围股票池，必须引入按日期生效的股票池快照，记录纳入/剔除日期、标的规则类型，并明确退市、ST、长期停牌、上市未满周期的处理规则。

### 7.3 策略复杂度纪律

10. **进 config = 已验证**：策略参数进入 `StrategyConfig` 的前提是已通过选参/验证流程并获得默认值；未经 sweep 验证的实验参数不得进 config 默认路径。
11. **参数预算**：单策略可调参数上限 8 个，超出必须在 PR 中论证为什么无法简化——参数越多，过拟合面越大。
12. **策略版本入账**：`momentum_window` 从 20 改到 60 本质上是换策略。`account.json` 应记录策略参数快照（新增只增不删的 `strategy_params` 字段，见 5.4 schema 规则），否则日后复盘时无法知道当时的盘是什么参数跑的。

### 7.4 模拟盘运维纪律

13. **状态只进不改**：`state/account.json` 禁止手工编辑、禁止回滚重跑历史区间；任何"修数据"需求只能以新账户文件重开。
14. **live 行为变更需重放等价检查**：触及 `run_day` 事件序/撮合/风控的改动，必须先用当前缓存 replay 一段历史区间，证明改动前后结果一致，或解释差异来源。
15. **补偿逻辑不得破坏幂等**：`_refetch_laggards` 这类"数据晚发布"的补偿逻辑，必须维持"重跑同日 no-op"不变量；相关场景应纳入 `TestRunDailyIdempotency`。

### 7.5 结论表述纪律

16. **样本量下限**：结论引用的 `closed_trades` < 10 或窗口 < 60 个交易日时，必须标注"样本不足，不具统计意义"；sweep 输出中的 `closed` 列不允许被无视。
17. **指标口径冻结**：`compute_metrics` / `trade_stats` 输出的 key 语义冻结，新指标只增不改名不改义。
18. **不承诺收益**：任何文档、报告、PR 描述不得声称、暗示或通过设计保证收益、零亏损或规避全部市场风险——harness 保证的是过程严谨（无未来函数、真实费用、风控约束、如实报告），不是盈利能力。
