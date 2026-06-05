# ADR 0002：将 Reservation 的 Confirm / Cancel 移出同步下单路径

- 状态：Accepted
- 日期：2026-06-04

## 背景

ADR 0001 已经去掉了一次同步商品查询，并把默认锁模式切到 `pessimistic`，这让热点路径有所改善。但 post-deploy perf lane 仍然暴露出第二个瓶颈：

- `reserve` 会持有热点行锁较长时间，导致订单请求在热点商品上排队数十秒
- client 和 k6 往往会在 `order-service` 完成整条 `reserve -> persist order -> confirm/cancel` 链路前就超时
- client 已经放弃后，后端仍可能继续执行延迟到达的 `confirm` 或 `cancel`
- teardown、reset、expire 逻辑随后可能与这些晚到的 terminalization 调用发生竞态
- 最终表现为可避免的 `404`、终态日志噪音，以及“库存状态变化”和“订单状态变化”之间更大的故障窗口

这不是一个“只有高压测档位才会暴露”的问题。即使是最保守的 smoke lane，也已经出现了需要把 terminalization 从同步路径拆出去的信号。

直接证据：

- GitHub Actions run：[`Flashsales Deploy` / `Postdeploy Runtime Gates` / `Concurrency Smoke (10 TPS)`](https://github.com/jingyi-zhao-01/homelab-cloud/actions/runs/26942234571/job/79486795441)
- 这条 job 页面显示：
  - lane 名称是 `Concurrency Smoke (10 TPS)`
  - `Process completed with exit code 2`
  - job 在 `2026-06-04` 失败，持续约 `7m 40s`

它说明的问题不是“10 TPS 很高”，而是：

- 当前热点路径的单位请求成本依然偏重
- `confirm/cancel` 的消费速度仍可能落后于前台请求进入速度
- 一旦 terminalization 积压，请求超时、重试、迟到调用和 teardown/reset 竞态就会连锁放大

当前 `order-service` 的同步路径是：

1. 校验用户
2. 在 `product-service` 里 `reserve` 库存
3. 持久化一笔 `pending` 订单
4. 处理支付
5. 同步调用 `confirm` 或 `cancel`
6. 更新订单最终状态

问题的关键不只是“有些 handler 是阻塞的”。更本质的问题是：reservation 的 terminalization 仍然绑定在单次请求生命周期里。

## 决策

我们将把 reservation 的 terminalization 移到异步路径。

### 目标形态

`order-service` 仍负责创建订单记录，并决定最终想要的业务结果，但它不再让 client 请求阻塞在对 `product-service` 的同步 `confirm` / `cancel` 调用上。

新的流程是：

1. `order-service` 先完成库存 `reserve`，并把订单持久化为 `pending`
2. `order-service` 决定期望的终态动作：
   - 支付成功时执行 `confirm`
   - 持久化失败或支付失败时执行 `cancel`
3. `order-service` 在与订单状态变更一致的持久化边界内，写入一条 durable 的 terminalization task
4. 后台 worker 消费这条 task，并调用 `product-service /reservations/{id}/confirm` 或 `/cancel`
5. worker 在成功后标记 task 完成；遇到瞬时失败时安全重试；重复执行应保持幂等无害

### 为什么这里必须引入 queue

这里引入 queue，不是为了“技术栈更高级”，而是为了把两类本质不同的责任拆开：

- 前台同步请求负责表达订单业务意图
- 后台异步 worker 负责把这个意图最终落实为 reservation terminalization

如果没有 durable queue，而继续把 `confirm/cancel` 绑在同步路径里，会同时出现几类问题：

1. client timeout 与业务正确性被耦合
   请求超时不代表系统已经停止执行；同步 terminalization 会把“用户是否还在等待”和“库存是否已经最终处理”绑死在一次请求生命周期里。

2. 后台落后无法被显式观测
   没有 queue 时，只能看到 `/orders` 变慢；有 queue 以后，才能明确看到 `queued`、`processing`、`retrying`、`error`、`succeeded` 这些状态。

3. 重试策略无处安放
   `confirm/cancel` 是跨服务副作用，天然需要 retry / backoff / stuck task 可见性；如果仍然放在同步路径里，这些机制会直接污染前台 latency。

4. 恢复能力太弱
   Pod 重启、worker 重启、request timeout、teardown/reset 与迟到调用竞态，都会放大为状态不一致风险。durable queue 至少能把“待执行工作”保留下来。

5. 10 TPS 也可能失败
   上面的 smoke lane 已经说明，是否需要 queue 不是由 TPS 数值表面大小决定，而是由“热点路径单位请求成本 + terminalization 消费速度 + 锁竞争”共同决定。

### 必须满足的性质

- 入队必须是 durable 的
- worker 重试必须是安全的
- `product-service confirm/cancel` 必须保持幂等
- 即使 terminalization 仍在执行中，order state 也必须能够表达“系统已经决定要做什么”
- 观测上必须能区分：
  - task queued
  - task running
  - task succeeded
  - task retrying
  - task dead-lettered 或 stuck

### 实现偏好

优先选择 durable queue 或 outbox-backed worker，而不是进程内 fire-and-forget task。

原因：

- API Pod 绑定的 fire-and-forget 在重启时可能直接丢失
- 当前问题正是在 contention、timeout、restart 这些场景里暴露出来的，而这些场景恰恰最依赖 durability
- queue 或 outbox 能提供明确的 retry、backlog 可见性，以及更安全的恢复行为

当前实现选择：

- queue owner：`order-service`
- durable medium：`PostgreSQL`
- queue implementation：`application/flashsale/order-service/queue.py`

这样做的原因是：

- 这条 queue 承载的是“订单工作流派生出的 terminalization 意图”，owner 应该是 `order-service`
- 先用 Postgres 跑通 durable queue，可以在不额外引入 broker 的前提下，把正确性、重试、可观测性和恢复语义先建立起来
- 后续如果需要迁移到 NATS JetStream、RabbitMQ 或其他 broker，也可以保留当前 owner 边界，只替换 queue backend

## 影响

预期收益：

- `order-service` 的用户请求路径更短
- 因等待 `confirm/cancel` 而导致的 client timeout 会减少
- teardown 或 expiry 与晚到 terminalization 调用竞态的概率会下降
- 运维信号会更清晰，因为 backlog 和 retry count 将变成可观测对象
- “订单意图”与“库存 terminalization 的实际执行”会更明确地解耦

代价与权衡：

- reservation terminalization 会变成 eventual consistency
- 需要额外引入 worker、durable task storage 和 replay 策略
- dashboard 和告警需要从请求指标扩展到 queue health
- 订单状态的消费者可能需要接受一段短暂的中间态

## 状态模型建议

同步模型里，`pending` 同时承担了两种含义：

- 订单业务结果还没决定
- 订单业务结果已经决定，但清理或确认动作还没执行完

异步模型应该把这两层语义拆开。一个可接受的形态是：

- 订单业务状态：`pending`、`confirmed`、`failed`、`cancelled`、`expired`
- reservation terminalization 状态：`queued`、`processing`、`succeeded`、`retrying`、`dead_letter`

这样既能保持用户可理解的订单语义，也能把后台异步工作的健康度暴露出来。

## 为什么这份 ADR 存在

这份 ADR 不是在说“只要把数据库驱动改成 async 就能解决热点问题”。

即使切到 async I/O：

- 热点商品行在 pessimistic locking 下依然会串行化
- 长时间 lock wait 依然会发生
- 如果 `confirm/cancel` 仍然在同步跨服务路径里，请求生命周期依然会被拉长

所以这里要做的是架构解耦，而不是只把函数签名改成 `async def`。

## 运维说明

Grafana 应该为这条异步路径增加专门的面板，至少包括：

- terminalization task backlog
- 最老 queued task 的 age
- 按 action type 统计的 retry count
- dead-letter count
- `confirm` / `cancel` 的 success rate 与 error rate
- reservation work 尚未完成的订单年龄分布

这些面板会成为后续排查“锁相关外溢症状”的主要入口。在热点流量下，如果 backlog 持续升高，或者 queued age 持续变大，就说明 reservation terminalization 路径开始落后于前台请求速度。

当前仓库里的 IaC 入口：

- Terraform module: `terraform/flashsale-grafana-dashboards`
- Dashboard: `Flashsale Async Terminalization`

## 时序图对比

这份 ADR 需要强调的不是“加了一个 worker”这么简单，而是请求时序发生了明确变化：

- 改造前：client 要一直等到 `confirm/cancel` 完成，请求路径把 reservation terminalization 也算在前台 latency 里
- 改造后：client 在 `reserve + persist order + durable enqueue` 完成后就可以返回，`confirm/cancel` 改由后台 worker 执行

高亮说明：

- 红色框：当前热点路径里应该被拿掉的同步 terminalization 段
- 绿色框：异步方案新增的 durable enqueue 段
- 蓝色框：改造后仍然存在，但已经移出请求路径的后台 terminalization 段

D2 源码： [0002-async-reservation-terminalization.d2](diagrams/0002-async-reservation-terminalization.d2)

渲染图： [0002-async-reservation-terminalization.svg](diagrams/0002-async-reservation-terminalization.svg)

## 相关变更

- `application/flashsale/order-service/queue.py`
- `application/flashsale/order-service/app/service.py`
- `application/flashsale/order-service/app/repositories.py`
- `application/flashsale/order-service/app/repository_postgres.py`
- `application/flashsale/order-service/app/repository_postgres_terminalization.py`
- `application/flashsale/product-service/app/main.py`
- `application/flashsale/product-service/app/service.py`
- `application/flashsale/product-service/app/repositories.py`
- `application/flashsale/docs/adrs/0002-1-order-confirmation-off-synchronous-path.md`
