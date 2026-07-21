# 03. 股票数据契约

## 1. 目的

数据契约让 InStock、适配器、分析脚本和 Agent 对字段、日期、单位和缺失行为达成一致。没有契约时，字段可用不等于口径可用。

## 2. 日线 OHLCV 最小契约

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `code` | string | 是 | 保留前导零，不使用整数 |
| `date` | date | 是 | 交易日，`YYYY-MM-DD` |
| `open` | float | 是 | 与 close 使用相同复权口径 |
| `high` | float | 是 | 应不低于 open/close/low |
| `low` | float | 是 | 应不高于 open/close/high |
| `close` | float | 是 | 与 open/high/low 同口径 |
| `volume` | float | 是 | 单位必须在数据集元数据说明 |
| `amount` | float | 否 | 成交额，明确货币和缩放 |
| `turnover_rate` | float | 否 | 明确是百分数还是小数 |
| `adj_factor` | float | 否 | 复权因子 |
| `source` | string | 否 | 实际源或缓存标识 |

## 3. 数据集元数据

每个数据集都要附：

```json
{
  "dataset": "daily_ohlcv",
  "market": "CN-A",
  "frequency": "1d",
  "timezone": "Asia/Shanghai",
  "as_of": "YYYY-MM-DD",
  "adjustment": "none|qfq|hfq",
  "volume_unit": "shares|lots|unknown",
  "amount_unit": "CNY|CNY_10k|unknown",
  "source": "instock",
  "source_detail": "actual provider or cache",
  "fetched_at": "ISO-8601",
  "row_count": 0,
  "symbol_count": 0,
  "input_sha256": "..."
}
```

`unknown` 必须显式写出，不能省略后由 Agent 自行猜测。

## 4. 逻辑约束

对每行：

```text
high >= max(open, close, low)
low  <= min(open, close, high)
open, high, low, close >= 0
volume >= 0
```

对数据集：

- `code + date` 唯一。
- 每个 code 的日期严格递增。
- `date` 落在交易日历内，或有明确异常说明。
- 单一数据集不能混合复权口径。
- 同一字段不能混合百分数和小数。
- `as_of` 等于实际最大有效日期，而不是抓取日期。

## 5. 缺失值处理

| 情况 | 动作 |
|---|---|
| 必填列完全缺失 | 失败 |
| 必填列部分空值 | 失败或隔离错误行，不能静默填充 |
| 可选列缺失 | 记录 `unavailable_fields`，相关分析降级 |
| 新上市历史不足 | 保留样本，但窗口指标为空 |
| 停牌导致无成交 | 按数据源语义处理，不能把缺失自动改为 0 |
| 上游空响应 | 失败，不输出空结果为“没有股票” |

## 6. 指标输出契约

本仓库只计算通用指标，不生成推荐：

| 字段 | 说明 |
|---|---|
| `sma_5/10/20` | 简单移动均线 |
| `ema_12/26` | 指数移动均线 |
| `macd` | `ema_12 - ema_26` |
| `macd_signal` | MACD 的 9 期 EMA |
| `macd_hist` | `macd - macd_signal` |
| `rsi_14` | Wilder 风格滚动 RSI |
| `atr_14` | 14 期平均真实波幅 |
| `volume_ma_5` | 5 期成交量均值 |
| `volume_ratio_5` | 当期成交量 / 前 5 期均量 |

约束：

- 所有指标按 code 分组、按 date 排序。
- 窗口内只使用当前及过去数据。
- 历史不足时返回空值，不使用未来数据回填。
- 代码版本和参数写入 manifest。
- 指标值不是交易信号。

## 7. 查询结果契约

```json
{
  "query_time": "2026-07-21T18:00:00+08:00",
  "as_of": "2026-07-21",
  "expected_as_of": "2026-07-21",
  "source": "instock",
  "scope": {
    "market": "CN-A",
    "symbols": "approved universe",
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD"
  },
  "method": {
    "tool": "stock-data-agent",
    "version": "0.1.0"
  },
  "row_count": 0,
  "field_coverage": {},
  "freshness": "fresh|stale|unknown",
  "quality": "pass|fail|degraded",
  "result": {},
  "limitations": []
}
```

## 8. 时间和交易日

- 系统业务时区固定为 `Asia/Shanghai`。
- 日线 `date` 不携带时区，但其含义由市场和业务时区决定。
- `query_time`、`fetched_at`、`started_at` 必须带偏移。
- 交易日历文件至少包含 `date,is_open`；生产应来自可靠交易日源并记录版本。
- 日线准备时间不是交易所收盘时间；应以数据刷新实际完成时间为准。当前建议默认 17:45，后续用任务监控调整。

## 9. 复权

分析前必须选择：

- `none`：原始价格，适合复盘原始成交和涨跌停约束；
- `qfq`：前复权，便于观察连续收益，但历史价格会随新事件变化；
- `hfq`：后复权，便于长期序列，但价格不等于当时可交易价格。

不能在同一结果中混合复权数据。涉及收益计算时必须解释现金分红和复权处理。

## 10. 质量报告

最小格式：

```json
{
  "ok": false,
  "row_count": 0,
  "symbol_count": 0,
  "actual_as_of": null,
  "freshness": "unknown",
  "required_columns": [],
  "missing_columns": [],
  "field_coverage": {},
  "duplicate_key_rows": 0,
  "invalid_ohlc_rows": 0,
  "negative_value_rows": 0,
  "errors": [],
  "warnings": []
}
```

错误样本只保留少量行并脱敏，避免日志膨胀和敏感数据泄漏。
