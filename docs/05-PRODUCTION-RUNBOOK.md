# 05. 生产值守与故障处理

## 1. 现场快照

截至 2026-07-21：

| 项目 | 状态 | 证据 |
|---|---|---|
| 服务器 | 正常 | 连续运行约 160 天 |
| 内存 | 充足 | 15 GiB 总内存，约 9.2 GiB 可用；Swap 已使用 |
| 根盘 | 充足 | 约 296 GiB，使用约 17% |
| OpenClaw | 正常 | 用户服务 active，6701 回环监听 |
| InStock | 正常 | app/db 容器 healthy，9988 HTTP 200 |
| 适配器 | 正常 | `health` 和合成 `self-test` 通过 |
| 核心刷新 | 异常 | 7 月 17、20 日数据处理提前失败 |
| 开盘提醒 | 有隐患 | 7 月 21 日引用 7 月 16 日旧清单 |
| 看门狗 | 正常 | 每 8 分钟检查，最近执行成功 |
| 自动备份 | 未验收 | 无恢复演练证据 |

该表只是一张快照。每次值守要重新检查。

## 2. 登录和主机检查

```bash
ssh yinxing-1
hostname
date -Is
uptime
free -h
df -h /
ss -lntp | grep -E ':(6701|9988)\b'
```

预期：6701、9988 只在 `127.0.0.1`；数据库没有公网端口。

## 3. InStock

```bash
sudo docker compose -f /opt/instock/compose.yaml ps
sudo docker compose -f /opt/instock/compose.yaml logs --tail=200 app
sudo docker compose -f /opt/instock/compose.yaml logs --tail=200 db
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9988/
```

只重启应用：

```bash
sudo docker compose -f /opt/instock/compose.yaml restart app
sudo docker compose -f /opt/instock/compose.yaml ps
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9988/
```

先保留日志再重启。重启成功仍需验证最大数据日期。

## 4. 生产适配器

```bash
ADAPTER=/opt/openclaw-bots/instances/hawkeye001/clawd/skills/instock-stock-screener/scripts/instock_screener.py
python3 "$ADAPTER" health
python3 "$ADAPTER" self-test
```

- `health`：InStock 是否可达。
- `self-test`：合成样本能否走通适配器逻辑。
- 二者都不能证明生产数据 fresh。

业务专用命令、预设和参数不进入共享交接仓库。

## 5. OpenClaw

```bash
sudo -u ubuntu env \
  HOME=/opt/openclaw-bots/instances/hawkeye001 \
  XDG_RUNTIME_DIR=/run/user/1000 \
  systemctl --user status openclaw-hawkeye001.service --no-pager

sudo -u ubuntu env \
  HOME=/opt/openclaw-bots/instances/hawkeye001 \
  XDG_RUNTIME_DIR=/run/user/1000 \
  journalctl --user -u openclaw-hawkeye001.service -n 200 --no-pager
```

机器人不响应时先区分：

```text
OpenClaw 层：服务/端口/模型请求
适配器层：命令/字段/超时
InStock 层：Web/数据库/刷新
数据源层：空响应/schema/限流
```

不要用重启 OpenClaw 掩盖数据刷新失败。

## 6. 开盘监测和看门狗

```bash
sudo -u ubuntu env HOME=/opt/openclaw-bots/instances/hawkeye001 XDG_RUNTIME_DIR=/run/user/1000 \
  systemctl --user status hawkeye001-stock-open-monitor.timer --no-pager

sudo -u ubuntu env HOME=/opt/openclaw-bots/instances/hawkeye001 XDG_RUNTIME_DIR=/run/user/1000 \
  journalctl --user -u hawkeye001-stock-open-monitor.service -n 120 --no-pager

sudo -u ubuntu env HOME=/opt/openclaw-bots/instances/hawkeye001 XDG_RUNTIME_DIR=/run/user/1000 \
  systemctl --user status hawkeye001-watchdog.timer --no-pager
```

一次性 service 执行后显示 inactive 可能正常；同时看 timer、最近退出码和日志。

日志分享前去除收件人、候选内容、Token、Cookie 和服务器秘密。

## 7. 数据新鲜度闸门

生产发送前检查：

```text
calendar expected_as_of
actual data as_of
candidate/watchlist as_of（如存在）
core refresh status
required field coverage
quality errors
```

推荐伪代码：

```python
if refresh_status != "success":
    block("core refresh failed")
elif actual_as_of is None:
    block("as_of unknown")
elif actual_as_of < expected_as_of:
    block("stale data")
elif required_coverage < min_coverage:
    block("field coverage insufficient")
elif quality_errors:
    block("data quality failed")
else:
    allow()
```

阻断告警应包含缺少日期、最近成功日期、失败阶段和下次重试，不包含候选清单。

## 8. 故障矩阵

| 现象 | 先检查 | 常见原因 | 动作 |
|---|---|---|---|
| 9988 无响应 | app 状态、日志、端口 | 应用崩溃/启动未完 | 保存日志，仅重启 app，复测 |
| 容器健康但数据旧 | 刷新退出码、最大日期 | 上游失败/任务提前退出 | stale，阻断下游，修数据链 |
| 空对象/字段错误 | 原始响应、schema、覆盖 | 上游漂移或空响应 | 保存脱敏样本，加显式检查 |
| 适配器缺字段 | `unavailable_fields` | 数据未补齐/字段改名 | 降级回答，不补造 |
| OpenClaw 不响应 | 用户服务、6701、日志 | 锁/模型/服务异常 | 分层定位后最小重启 |
| 开盘提醒用旧数据 | `as_of`、刷新状态 | 无 freshness gate | 停正常提醒，发 stale 告警 |
| service inactive | timer、退出码、日志 | 一次性任务完成 | 成功则正常，不常驻化 |
| 分析很慢 | 内存、Swap、规模 | 重复读取/并发过高 | 复用快照、分批、限流 |

## 9. 监控指标

| 指标 | 建议 |
|---|---|
| InStock HTTP | 200 |
| 数据日期 | 最近应有交易日 |
| 核心刷新 | 成功；当前常见约 50—70 秒 |
| OpenClaw | active，6701 回环监听 |
| 开盘监测 | timer active，最近 service 成功 |
| 根盘 | < 80% |
| 可用内存 | > 2 GiB |
| Swap | 不持续增长 |
| 数据备份 | 24 小时内成功且可校验 |

刷新耗时突然极短且任务提前退出也要报警，不能只监控超时。

## 10. 值守报告模板

```text
【股票数据分析值守】
检查时间：<ISO-8601, Asia/Shanghai>
总体状态：正常 / 异常 / 降级

1. InStock：<容器 + HTTP>
2. 数据新鲜度：expected_as_of=<日期>，actual_as_of=<日期>
3. 最近刷新：<时间、退出状态、耗时>
4. 字段覆盖/质量：<通过/降级/失败>
5. OpenClaw/适配器：<状态>
6. 开盘监测：<timer、最近执行、闸门状态>
7. 资源：<内存、磁盘、Swap>
8. 需要处理：<P0/P1/P2>

说明：已去除策略、候选、密钥和收件人信息。
```
