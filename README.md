# 股票数据分析 Agent 接手包

这是新奥机器人股票数据能力的脱敏接手仓库。它从 [myhhub/stock](https://github.com/myhhub/stock)（InStock）开始，说明当前生产部署、数据获取、数据质量、新鲜度判断、指标计算、Agent 调用、故障处置和验收方法，并提供一套可测试的通用工具代码。

> 范围只有股票数据与确定性分析。本仓库不包含新闻分析，不包含任何私有选股/交易策略、参数、候选清单、回测结论、私有提示词、收件人标识或密钥，也不执行真实交易。

## 1. 接手结论

接手难度为**中等**：

- 只读查询、健康检查、运行现成分析：熟悉 SSH、Docker、Python 和 systemd 的 Agent 通常半天可以开始值守。
- 稳定维护数据链和可复现分析：通常需要 1—2 个工作日熟悉字段、交易日、新鲜度和供应商异常。
- 新增生产级数据管道：通常需要 3—5 个工作日完成数据字典、测试、调度、监控、回滚和验收。

机器资源足以承载当前日频刷新、数十只标的盘中监测、中小规模指标计算和单 Agent 查询。主要风险不是算力，而是：

1. 公共数据源会超时、返回空对象或变更字段。
2. 容器健康不代表数据已经刷新。
3. 交易日和数据可用时间判断错误，会把旧数据写成“今天”。
4. 大模型如果跳过确定性计算，容易补造字段或误读口径。

## 2. 当前必须先处理的 P0

现场快照时间为 2026-07-21（Asia/Shanghai）：

- 2026-07-17、2026-07-20 的 InStock 核心刷新在数据处理阶段提前失败。
- 2026-07-21 的开盘提醒仍引用 2026-07-16 的旧清单。
- InStock Web、数据库容器和只读适配器本身仍可访问。

因此，新 Agent 的第一项生产改造不是增加新指标，而是落地 `freshness gate`：

```text
服务健康 + 数据达到最近应有交易日 + 核心刷新成功 -> fresh -> 允许下游
数据日期落后                                      -> stale -> 禁止正常下游
无法取得数据日期或刷新状态                         -> unknown -> 禁止正常下游
```

`stale` 或 `unknown` 时必须 fail closed：只发数据异常告警，不发正常推荐或实时提醒。

本仓库的 `stock-data-agent report` 已实现基础闸门；生产接入时还要把 InStock 核心刷新退出状态加入最终判定。

## 3. 仓库内容

```text
.
├── README.md
├── config.example.yaml
├── pyproject.toml
├── docs/
│   ├── 01-INSTOCK-BASELINE.md
│   ├── 02-DATA-PIPELINE.md
│   ├── 03-DATA-CONTRACT.md
│   ├── 04-ANALYSIS-SOP.md
│   ├── 05-PRODUCTION-RUNBOOK.md
│   ├── 06-AGENT-HANDOFF.md
│   └── 07-SECURITY-BOUNDARY.md
├── src/stock_data_agent/
│   ├── cli.py
│   ├── freshness.py
│   ├── indicators.py
│   ├── instock_client.py
│   └── quality.py
├── examples/ohlcv_sample.csv
└── tests/
```

文档阅读顺序：

1. [InStock 基线与当前部署](docs/01-INSTOCK-BASELINE.md)
2. [数据管道与职责边界](docs/02-DATA-PIPELINE.md)
3. [数据契约](docs/03-DATA-CONTRACT.md)
4. [数据分析 SOP](docs/04-ANALYSIS-SOP.md)
5. [生产值守与故障处理](docs/05-PRODUCTION-RUNBOOK.md)
6. [Agent 接手规范](docs/06-AGENT-HANDOFF.md)
7. [安全和脱敏边界](docs/07-SECURITY-BOUNDARY.md)

## 4. 从 myhhub/stock 开始理解

InStock 是开源股票数据与分析系统，上游具备日线/ETF 数据采集、技术指标和形态计算、数据筛选、验证/回测、Web 界面、数据库存储、批处理和可选交易模块。

本机使用方式与上游默认部署不同：

- InStock 被当作独立数据与计算子系统，不直接作为大模型知识。
- Web 只监听 `127.0.0.1:9988`，不对公网开放。
- MariaDB 不发布宿主机端口。
- 交易服务关闭。
- 生产目录带宿主机覆盖层，不能直接删除后照搬上游重装。
- 为降低公共数据源压力，主要在工作日收盘后刷新。
- 机器人通过批准的只读适配器获得结构化结果。

本仓库核对的上游基线：

```text
repository: myhhub/stock
commit: b6e0ca2268cfbadd02f5ed052159c187b6670231
commit time: 2026-04-02T10:25:44+08:00
license: Apache-2.0
```

上游将来可能变化；升级时以 Git commit 和容器镜像摘要为准，不用“最新版”作为可审计版本号。

## 5. 当前生产架构

```text
用户 / 微信
    |
    v
OpenClaw hawkeye001 (127.0.0.1:6701)
    |  意图识别、权限判断、结果解释
    v
批准的只读股票适配器
    |  健康、字段覆盖、结构化结果
    v
InStock Web/API (127.0.0.1:9988)
    |
    +-- InStock 应用容器
    +-- MariaDB 容器（仅内部网络）
    +-- 公共数据源 / 缓存 / 工作日刷新

独立支路：开盘监测定时器 -> 实时行情检查 -> 微信提醒
隔离支路：私有业务策略层 -> 所有者管理，本仓库不接触
```

关键路径：

| 对象 | 路径 | 用途 |
|---|---|---|
| InStock 部署 | `/opt/instock` | Compose、覆盖层、数据和运维说明 |
| 运维说明 | `/opt/instock/README.ops.md` | 启停、状态、日志、健康检查 |
| OpenClaw 实例 | `/opt/openclaw-bots/instances/hawkeye001` | 服务用户 HOME 和运行状态 |
| Agent 工作区 | `/opt/openclaw-bots/instances/hawkeye001/clawd` | Agent 规则、工具和研究任务 |
| 只读适配器 | `.../clawd/skills/instock-stock-screener/scripts/instock_screener.py` | 生产查询入口 |
| 密钥目录 | `/opt/instock/secrets` | 仅授权运维接触，不进入报告或仓库 |

## 6. 运行本仓库工具

要求 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

### 6.1 检查 InStock 是否在线

在服务器本机或 SSH 隧道环境执行：

```bash
stock-data-agent health --base-url http://127.0.0.1:9988
```

这只证明 Web 可达，不证明数据最新。

### 6.2 读取批准的 InStock 数据模块

InStock 上游提供只读 GET 数据接口：`/instock/api_data?name=<module>&date=<date>`。接口没有足够的生产级鉴权，因此本工具默认只允许回环地址，并要求模块名出现在配置白名单中。

先复制配置：

```bash
cp config.example.yaml config.yaml
```

由所有者/运维人员在 `allowed_modules` 中填写已经批准的数据模块。不要把私有模块名写入公共文档。

```bash
stock-data-agent fetch-module \
  --config config.yaml \
  --module APPROVED_MODULE \
  --date 2026-07-21 \
  --output data/raw/module-2026-07-21.json
```

工具只执行 GET、限制响应体大小、拒绝未批准模块，并在快照旁写元数据。生产 Agent 不应直接向 MariaDB 拼 SQL。

### 6.3 质检 OHLCV

CSV 至少包含：`code,date,open,high,low,close,volume`。可选字段包括 `amount,turnover_rate,adj_factor,source`。

```bash
stock-data-agent validate \
  --input examples/ohlcv_sample.csv \
  --output outputs/quality.json
```

质检包括：

- 必填字段和类型；
- `code + date` 重复；
- OHLC 逻辑关系；
- 负数价格/成交量；
- 每个标的日期单调性；
- 数据最大日期和新鲜度；
- 字段覆盖率和错误样本。

### 6.4 计算通用指标

```bash
stock-data-agent indicators \
  --input examples/ohlcv_sample.csv \
  --output outputs/indicators.csv
```

工具提供 SMA、EMA、MACD、RSI、ATR、成交量均值与量比等通用指标。它不内置筛选阈值、不排序推荐股票、不生成买卖信号。

### 6.5 一次完成质检、闸门和指标分析

```bash
stock-data-agent report \
  --input examples/ohlcv_sample.csv \
  --output-dir outputs/run-001 \
  --now 2026-07-21T18:00:00+08:00
```

输出：

```text
outputs/run-001/
├── quality.json
├── indicators.csv
├── latest_snapshot.csv
└── manifest.json
```

`manifest.json` 记录：运行时间、输入哈希、行数、数据日期、freshness、工具版本和输出文件。质检错误或 stale/unknown 时命令返回非零退出码，便于 systemd/cron 阻断下游。

## 7. 每次数据分析必须遵守的流程

```text
明确问题
  -> 固定市场/代码/时间/频率/复权/时点
  -> 检查服务健康
  -> 获取原始快照并记录来源
  -> 验证新鲜度和字段覆盖
  -> 质量检查
  -> 确定性脚本计算
  -> 验证无未来数据/口径错误
  -> 生成 manifest 和结果
  -> Agent 区分事实、分析、限制、建议
```

标准结果至少包含：

```json
{
  "query_time": "ISO-8601 with timezone",
  "as_of": "YYYY-MM-DD",
  "source": "actual source or cache",
  "scope": "market, symbols and date range",
  "method": "tool and version",
  "row_count": 0,
  "field_coverage": {},
  "freshness": "fresh|stale|unknown",
  "result": {},
  "limitations": []
}
```

如果没有 `as_of`、来源或字段覆盖，Agent 不能把结果包装成确定性结论。

## 8. Agent 的职责

接手 Agent 负责：

- 数据服务和只读接口健康检查；
- 数据新鲜度、字段覆盖和异常检测；
- 用确定性程序计算指标和统计；
- 记录数据日期、来源、口径、输入哈希和工具版本；
- 把工具结果解释成清晰回答；
- 遇到 stale/unknown 时阻断正常下游并告警；
- 形成可复现、可审计的分析工作区。

接手 Agent 不负责：

- 私有选股或交易策略；
- 候选清单和策略参数；
- 新闻分析；
- 微信收件人和主动发送权限；
- 券商连接、订单和真实交易；
- 密钥、Cookie、Token 的读取或输出。

## 9. 生产值守快速命令

```bash
# 主机
ssh yinxing-1
date -Is
uptime
free -h
df -h /

# InStock
sudo docker compose -f /opt/instock/compose.yaml ps
sudo docker compose -f /opt/instock/compose.yaml logs --tail=200 app
sudo docker compose -f /opt/instock/compose.yaml logs --tail=200 db
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9988/

# 生产只读适配器
python3 /opt/openclaw-bots/instances/hawkeye001/clawd/skills/instock-stock-screener/scripts/instock_screener.py health
python3 /opt/openclaw-bots/instances/hawkeye001/clawd/skills/instock-stock-screener/scripts/instock_screener.py self-test
```

`self-test` 使用合成样本，只证明代码路径可以运行，不证明生产数据最新。

更多命令见 [生产 Runbook](docs/05-PRODUCTION-RUNBOOK.md)。

## 10. 新分析任务目录

```text
/opt/openclaw-bots/instances/hawkeye001/clawd/research/<new-task>/
├── README.md
├── requirements.txt
├── config.example.yaml
├── data/
│   ├── raw/          # 原始快照，只读
│   └── processed/    # 清洗和衍生数据
├── src/              # 获取、清洗、计算、报告
├── tests/            # 单元测试和数据断言
├── outputs/          # CSV/JSON/图表/报告
└── validation.json   # 时间、日期、哈希、行数和检查结果
```

新 Agent 必须新建目录，不遍历、复用或概括所有者现有私有研究目录。

## 11. 接手验收

完成以下演示才算可独立值守：

- 解释 InStock、MariaDB、只读适配器、OpenClaw 和监测任务之间的关系。
- 在不输出秘密的情况下完成主机、容器、端口和接口检查。
- 识别“服务健康但数据过期”，并阻断正常下游。
- 对合成数据完成 `validate -> indicators -> report`。
- 处理空响应、缺字段、重复数据和 OHLC 错误，不让模型补造。
- 为一次新分析生成 `manifest.json` 和输入哈希。
- 区分事实、分析和建议，不做收益保证。
- 给出不含策略、候选、密钥和收件人标识的值守报告。

## 12. 当前容量边界

当前服务器约 15 GiB 内存、根盘使用率约 17%，适合日频数据、数十只标的盘中监测和轻量历史分析。以下任务应拆分或限流：

- 分钟级全市场持续抓取；
- 多 Agent 重复扫描全市场；
- 大量参数网格或机器学习训练；
- 在生产 MariaDB 上执行长时间无索引查询。

建议共享行情快照、缓存中间结果、队列化重任务、限制并发和内存；重型研究放到离线工作区或独立计算机。

## 13. 脱敏确认

发布前运行：

```bash
rg -n '(token|password|secret|cookie|wxid|recipient)' . \
  -g '!README.md' -g '!docs/07-SECURITY-BOUNDARY.md'
git diff --cached
```

发现真实值立即停止提交。仅出现字段名、示例占位符或安全说明不代表泄密，但仍需人工复核。

---

状态说明：本仓库的现场信息只对 2026-07-21 有效；每次接手和事故汇报必须重新读取生产状态，不得照抄旧快照。
