# 01. InStock 基线与当前部署

## 1. 上游项目

- 仓库：[myhhub/stock](https://github.com/myhhub/stock)
- 产品名：InStock
- 许可：Apache-2.0
- 本次核对 commit：`b6e0ca2268cfbadd02f5ed052159c187b6670231`
- commit 时间：`2026-04-02T10:25:44+08:00`

上游主要组成：

```text
instock/core/crawling/   公共数据源抓取
instock/core/indicator/  指标计算
instock/core/kline/      K 线和可视化
instock/job/             日常批处理
instock/web/             Tornado Web/API
instock/lib/             数据库和通用库
instock/trade/           可选交易模块
docker/                  容器部署
cron/                    周期任务
```

本接手包只使用数据、指标、Web/API 和批处理能力。交易模块不启用，策略目录不纳入接手。

## 2. 上游 Web/API 行为

上游 Web 服务由 Tornado 提供，默认端口为 9988。与数据接手最相关的只读接口是：

```text
GET /
GET /instock/
GET /instock/api_data?name=<module>&date=<YYYY-MM-DD>
GET /instock/data/indicators?code=<code>&date=<date>&name=<name>
```

注意：

- `/instock/api_data` 的 `name` 会映射到内部 Web 模块和数据库表。
- 未提供日期时，某些模块可能返回整表，响应体会很大。
- 指标路由主要返回 HTML，可用于人工查看 K 线，不是稳定的结构化 API 契约。
- 上游应用没有满足互联网暴露要求的认证与限流层。
- 上游还存在可写控制路由；生产 Agent 不得调用。

因此生产约束是：

1. Web 只监听回环地址。
2. 只读客户端只允许 GET。
3. 模块名必须配置白名单。
4. 设置超时、响应体上限和行数上限。
5. 所有响应先保存原始快照，再做分析。
6. 对结构化接口建立自己的字段契约，不直接依赖页面 HTML。

## 3. 当前生产部署

部署目录：`/opt/instock`

```text
/opt/instock/
├── compose.yaml
├── README.ops.md
├── config/
├── data/
├── overrides/
├── secrets/
└── backups/
```

容器：

| 容器 | 作用 | 网络 |
|---|---|---|
| `instock-app` | Web、批处理、定时任务 | `127.0.0.1:9988` |
| `instock-db` | MariaDB | 仅 Compose 内部网络 |

当前镜像使用固定 digest。升级记录至少包含：

```json
{
  "upstream_commit": "...",
  "app_image_digest": "sha256:...",
  "database_image_digest": "sha256:...",
  "compose_sha256": "...",
  "overrides_sha256": "...",
  "migration": "none|description",
  "tested_at": "ISO-8601",
  "rollback_point": "..."
}
```

## 4. 当前与上游默认状态的差异

| 项目 | 上游默认倾向 | 当前生产 |
|---|---|---|
| Web 暴露 | 容器端口可映射到外部 | 只绑定 `127.0.0.1:9988` |
| 数据库 | Compose 内数据库 | 不发布宿主机端口 |
| 刷新频率 | 包含小时和日任务 | 以工作日收盘后刷新为主 |
| 交易模块 | 上游提供可选实现 | 关闭 |
| 数据源 | 依赖多个公开源 | 根据服务器网络可用性做回退 |
| 本地修改 | 上游源代码 | `/opt/instock/overrides` 保留适配 |
| 机器人调用 | 上游无 OpenClaw 约束 | 通过批准的只读适配器 |

## 5. 不要直接重装的原因

`/opt/instock` 不是 Git 工作树，生产版本由三个维度共同决定：

1. 容器镜像摘要；
2. 宿主机 Compose/配置；
3. 本地覆盖层。

直接删除目录后从上游重装会丢失网络适配、调度、权限和安全边界。正确升级流程：

```text
记录当前状态
  -> 可恢复备份
  -> 在隔离环境拉取新上游/镜像
  -> 逐项重放覆盖层
  -> 运行健康、字段、新鲜度和回归测试
  -> 小窗口发布
  -> 验证定时任务
  -> 保留回滚点
```

## 6. SSH 隧道访问 Web

```bash
ssh -L 9988:127.0.0.1:9988 yinxing-1
```

然后在本地访问 `http://127.0.0.1:9988/`。

禁止为了方便把 9988 改成 `0.0.0.0`。如果未来确需多用户访问，应增加反向代理、身份认证、TLS、访问审计和速率限制，而不是直接暴露上游应用。

## 7. 接手时要记录的基线

```bash
date -Is
hostname
uname -a
docker version
sudo docker compose -f /opt/instock/compose.yaml ps
sudo docker inspect instock-app --format '{{.Image}}'
sudo docker inspect instock-db --format '{{.Image}}'
sha256sum /opt/instock/compose.yaml
du -sh /opt/instock/data /opt/instock/overrides /opt/instock/backups
```

输出不得包含完整 Compose 展开配置、环境变量或 secret 内容。
