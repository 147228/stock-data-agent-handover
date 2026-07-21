# 02. 股票数据管道与职责边界

## 1. 数据流

```text
公开数据源
  -> InStock 抓取/解析
  -> MariaDB / 本地缓存
  -> InStock Web/API 或批准的只读适配器
  -> 原始快照 data/raw
  -> schema + 质量 + freshness
  -> 标准化 data/processed
  -> 确定性指标计算
  -> outputs + manifest
  -> Agent 解释
  -> 用户 / 经批准的提醒链路
```

每一层必须有明确职责：

| 层 | 负责 | 不负责 |
|---|---|---|
| 数据源 | 返回原始市场数据 | 保证永久稳定 |
| InStock | 抓取、存储、基础指标 | 替 Agent 判断数据是否适合当前问题 |
| 只读适配器 | 白名单、字段映射、结构化输出 | 私有策略决策 |
| 质量层 | 日期、schema、完整性、一致性 | 生成不存在的字段 |
| 分析层 | 确定性计算、统计、验证 | 用自然语言猜数值 |
| Agent | 编排、解释、风险说明 | 绕过闸门、真实交易 |

## 2. 任务状态机

建议所有数据任务采用同一状态机：

```text
CREATED
  -> FETCHING
  -> SNAPSHOT_SAVED
  -> VALIDATING
  -> READY
  -> ANALYZING
  -> REPORTED

任意阶段失败 -> FAILED
数据日期落后 -> STALE
无法判断日期 -> UNKNOWN
```

状态记录示例：

```json
{
  "run_id": "20260721T180000+0800-daily-bars",
  "task": "daily-bars",
  "status": "READY",
  "started_at": "2026-07-21T18:00:00+08:00",
  "finished_at": "2026-07-21T18:00:42+08:00",
  "source": "instock",
  "source_as_of": "2026-07-21",
  "expected_as_of": "2026-07-21",
  "freshness": "fresh",
  "input_sha256": "...",
  "row_count": 0,
  "errors": [],
  "warnings": []
}
```

## 3. 原始快照原则

`data/raw` 中的文件必须满足：

- 下载后不再原地修改。
- 文件名包含数据域和 `as_of`。
- 同时保存来源、请求参数、HTTP 状态、抓取时间和 SHA-256。
- 请求头和元数据不得保存 Cookie、Token 或个人标识。
- 相同请求重复抓取时使用新的 `run_id`，不覆盖旧证据。

推荐结构：

```text
data/raw/2026-07-21/
├── daily-bars.json
├── daily-bars.meta.json
├── universe.json
└── universe.meta.json
```

元数据示例：

```json
{
  "fetched_at": "2026-07-21T18:00:00+08:00",
  "source": "instock",
  "endpoint": "/instock/api_data",
  "params_redacted": {"name": "APPROVED_MODULE", "date": "2026-07-21"},
  "http_status": 200,
  "content_type": "application/json",
  "bytes": 12345,
  "sha256": "..."
}
```

## 4. 标准化层

标准化只做口径统一，不做业务筛选：

- 代码统一为字符串，保留前导零。
- 日期统一为 `YYYY-MM-DD`，时间为带时区 ISO-8601。
- 金额和成交量单位在列名或元数据中明确。
- 将上游 OADate 日期转换为 ISO 日期。
- 价格列转换为数值；无法转换的行进入错误报告。
- 明确复权方式，不允许混合不同复权口径。
- 去重策略必须写明，不静默保留任意一条。

标准化输出不覆盖原始快照：

```text
data/processed/<dataset>/<as_of>/part-*.parquet
data/processed/<dataset>/<as_of>/schema.json
data/processed/<dataset>/<as_of>/quality.json
```

## 5. 新鲜度判定

日线数据不能简单用“今天日期”判断：

- 交易日收盘并完成刷新后，期望日期为当天。
- 交易日刷新前，期望日期通常为前一交易日。
- 周末/节假日，期望日期为最近交易日。
- 临时休市或数据源延迟要由交易日历和刷新状态共同判断。

生产必须使用交易所日历或经验证的交易日表。仓库代码在无日历时只能退化为工作日判断，并在报告中标记 `calendar_quality=weekday_only`。

最终新鲜度不能只比较日期，还要检查：

```text
actual_as_of >= expected_as_of
AND core_refresh_status == success
AND required_field_coverage >= threshold
AND quality_errors == 0
```

任一未知即 `unknown`；禁止把 unknown 当 fresh。

## 6. 字段漂移处理

公共源常见变化：

- 字段改名；
- 数字变成字符串；
- 空响应；
- 顶层从列表变成对象；
- 嵌套层级变化；
- 单位改变；
- 只返回部分标的。

处理顺序：

1. 保存脱敏原始响应和 Content-Type。
2. 在解析前检查顶层类型和关键键。
3. 对字段别名做显式映射。
4. 缺少必填字段立即失败。
5. 可选字段缺失进入 `unavailable_fields`。
6. 增加回归样本和测试。
7. 恢复后验证日期、行数和字段覆盖，而不只看 HTTP 200。

## 7. 失败和重试

可重试：

- 短时网络错误；
- 429/5xx；
- 连接重置；
- 明确的临时上游故障。

不可盲目重试：

- schema 不匹配；
- 必填字段缺失；
- 日期落后；
- 身份认证失败；
- 返回 HTML 而预期 JSON；
- 数据逻辑错误。

建议指数退避并加抖动，最多 3 次。重试之间不修改原始证据。连续失败后进入事件处理，不无限循环。

## 8. 幂等和并发

- `run_id` 唯一。
- 相同数据域/日期同一时间只允许一个写任务。
- 使用文件锁或调度器互斥，锁有超时和所有者信息。
- 分析任务可以并发读取同一不可变快照。
- 不让多个 Agent 重复全市场抓取；复用已验证快照。
- 发送链路使用独立 dedupe key：`channel + task + as_of + message_type`。

## 9. 备份与恢复

当前未验收自动数据库备份。建议：

- 日刷新成功后做加密逻辑备份。
- 保留 7 份日备、4 份周备。
- 配置、覆盖层与数据库分别备份。
- 每季度在隔离环境恢复并比对表数、行数、日期和校验和。

“目录中有备份文件”不等于“可恢复”。只有最近成功时间、完整性校验和恢复演练均存在时，备份状态才是正常。
