# ADR 0003：将 `order-worker` 从 `order-api` 中拆分，并为各服务引入独立 DB pool 配置

- 状态：Accepted
- 日期：2026-06-04

## 背景

ADR 0002 和 ADR 0002-1 已经把最重的同步尾段从 `POST /orders` 的前台请求路径里移走：

- 0002 将 reservation 的 `confirm / cancel` 终态处理移到后台 worker
- 0002-1 进一步把 `/orders` 的确认尾段从同步路径里拆出去

但在真实 trace 和 Grafana Drilldown 里，我们又看到了新的瓶颈形态：

- `process terminalization batch` 会出现 20s 甚至更长的尖峰
- `POST /orders` 的 p90 会被拉高到数秒
- `terminate reservation` 会维持在 2-5s 左右
- 这些慢链路与前台下单请求发生在同一时间窗口

这说明问题不只是某个 SQL 慢，而是前台请求和后台 worker 仍然在同一个 `order-service` 进程 / Pod 里争抢资源。

当前结构里，`order-service` 同时承担：

1. HTTP API：`POST /orders`、`GET /orders`、health probes
2. 后台 worker：`process terminalization batch`

这会导致几类争用同时存在：

- 同一个 Pod 的 CPU 和线程调度
- 同一个应用级 DB connection pool
- 同一个 Postgres
- 同一批热点订单 / reservation / product 行
- 同一个 downstream `product-service` capacity

即使 queue 已经 durable，worker 仍然可能把前台请求拖慢，因为它和 API 还绑在一起。

另一个观察是：我们当前代码里虽然已经把数据库访问统一成了可配置连接池，但每个服务之前仍然是“每次 `psycopg.connect(...)` 现开现关”的模式。对于高频 worker 和高并发前台接口来说，这会把连接建立、借还、等待放大成尾延迟。

## 决策

我们将做两层隔离：

1. 将 `order-service` 拆成两个 workload：
   - `order-api`：只处理前台 HTTP 请求
   - `order-worker`：只处理 `process terminalization batch`
2. 为 `user-service`、`product-service`、`order-api`、`order-worker` 引入独立 DB pool 配置

### 新的部署边界

- `order-api`
  - 负责 `POST /orders`、`GET /orders`、`/ready`、`/live`
  - 允许更大的 DB pool
  - 作为 latency-sensitive workload

- `order-worker`
  - 只负责后台 terminalization batch
  - 允许更小的 DB pool
  - 作为 latency-insensitive workload

### 新的连接管理边界

每个服务都可以通过配置单独控制：

- `DB_POOL_MIN_SIZE`
- `DB_POOL_MAX_SIZE`
- `DB_POOL_TIMEOUT_SECONDS`

这样可以把前台和后台的资源策略分开：

- `order-api` 的 pool 可以略大，优先保证请求响应
- `order-worker` 的 pool 可以更小，避免抢占前台连接
- `product-service` 和 `user-service` 也可以按各自热度单独收敛

## 为什么这样做

这次调整的目标不是“多加一个 deployment”这么简单，而是把两个不同性质的负载分离：

- 前台 API 是 latency-sensitive，目标是短尾延迟和稳定的请求完成时间
- 后台 worker 是 throughput-oriented，目标是稳定消费、可重试、可回放

如果这两者还放在同一个 Pod 里，即使 queue 已经是 durable 的，也仍然会发生：

1. worker 批处理抢占 API 的 CPU 时间片
2. worker 批处理占用过多 DB 连接
3. worker 的长事务 / 重试放大整个进程的尾延迟
4. API 的 readiness / liveness 被后台负载间接影响

因此，真正有效的隔离必须发生在 deployment / workload 级别，而不是只在代码里把 worker thread 起出来。

## 具体实现

### `order-service`

- `app/main.py`
  - 通过 `ORDER_SERVICE_RUN_BACKGROUND_WORKER` 控制是否启动内置 worker
- `app/entrypoints/http_api.py`
  - 支持在 API 进程里关闭后台 worker
- `app/entrypoints/worker_main.py`
  - 新增独立 worker 入口
- `charts/flashsales/templates/order-deployment.yaml`
  - `order-api` 显式关闭后台 worker
- `charts/flashsales/templates/order-worker-deployment.yaml`
  - 新增 `order-worker` deployment
  - 使用同一镜像，但 command 切换为 worker 入口

### DB pool

- `flashsale/order-service/app/db_pool.py`
- `flashsale/product-service/app/db_pool.py`
- `flashsale/user-service/app/db_pool.py`

这些模块统一封装了连接池与 fallback 行为。

各服务的 repository / worker 热路径已切换到共享池，避免一个 service 内部的多个 repository 再各自建池造成连接数乘法增长。

### Helm 配置

- `charts/flashsales/templates/service-configmaps.yaml`
  - 为每个 service 注入各自的 pool 参数
- `charts/flashsales/values.yaml`
  - 为 `user-service`、`product-service`、`order-api`、`order-worker` 提供独立的池配置默认值

## 影响

预期收益：

- `order-api` 不再和后台 terminalization worker 争抢同一进程资源
- `POST /orders` 的尾延迟更容易回落
- `process terminalization batch` 的波动不再直接污染前台请求
- 每个服务可以单独调节 DB pool 大小，减少无谓的连接占用
- 连接数和连接等待更容易通过 Grafana / Postgres 观测定位

代价与权衡：

- 部署资源更多了，需要维护两个 `order-service` workload
- 配置项更多，需要关注每个服务的 pool size
- worker 和 API 的资源边界更清晰了，但也意味着要单独关注 worker 的 backlog 与健康状况

## 观测与运维

这次拆分后，Grafana 至少需要重点看：

- `POST /orders` 的 p90 / p95 / p99
- `process terminalization batch` 的持续时间和队列 backlog
- `terminate reservation` 的单次耗时
- PostgreSQL / pooler 的等待时间
- 各服务的 DB pool 使用率和 `max wait`

如果 `order-worker` 的池已经被压小，但 `order-api` 的尾延迟仍然不降，说明瓶颈已经不再是应用内争用，而更可能是：

- Postgres 热点行锁
- 下游 `product-service` 的 capacity
- 更深层的 SQL 或索引问题

## 为什么不是只改 queue 或只加 Kafka

这次问题的核心不是“queue 还不够高级”，而是**同一套代码 / 同一组资源同时承载了前台请求和后台消费**。

即使将 queue backend 从 Postgres 换成 Kafka，如果 `order-worker` 还是和 `order-api` 跑在一起，仍然会争抢：

- CPU
- DB pool
- downstream capacity

所以本次优先级更高的改动是隔离 workload，再逐步决定是否替换 queue backend。

## 时序图对比

这份 ADR 对应的关键变化是：

- `order-api` 只负责前台请求和 durable enqueue
- `order-worker` 只负责后台 batch consumption
- 连接池也从“整个服务共享一组默认直连”变成“每个 service 独立配置”

建议在图里用 step number 标注两条路径：

1. API 路径：用户下单、库存预留、订单写入、durable enqueue
2. Worker 路径：claim batch、terminalize reservation、回写订单状态
3. 配置路径：不同 workload 使用不同 DB pool 参数

## 相关变更

- `flashsale/order-service/app/main.py`
- `flashsale/order-service/app/entrypoints/http_api.py`
- `flashsale/order-service/app/entrypoints/worker_main.py`
- `flashsale/order-service/app/db_pool.py`
- `flashsale/order-service/app/adapters/order_postgres_repository.py`
- `flashsale/order-service/app/adapters/terminalization_task_postgres_repository.py`
- `flashsale/order-service/app/adapters/order_postgres_unit_of_work.py`
- `flashsale/product-service/app/db_pool.py`
- `flashsale/product-service/app/locking/inventory.py`
- `flashsale/product-service/app/repositories.py`
- `flashsale/user-service/app/db_pool.py`
- `flashsale/user-service/app/repositories.py`
- `charts/flashsales/templates/order-deployment.yaml`
- `charts/flashsales/templates/order-worker-deployment.yaml`
- `charts/flashsales/templates/service-configmaps.yaml`
- `charts/flashsales/values.yaml`
