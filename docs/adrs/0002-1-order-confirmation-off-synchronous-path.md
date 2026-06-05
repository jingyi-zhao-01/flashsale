# ADR 0002-1：将 `/orders` 的确认步骤移出同步请求路径

- 状态：Accepted
- 日期：2026-06-04

## 背景

这份 ADR 不是一条新的架构方向，而是 ADR 0002 的延伸。

ADR 0002 已经把 reservation 的 `confirm / cancel` 从同步下单路径里拆出去，让后台 worker 去处理 terminalization。
但在 `order-service` 的真实 `POST /orders` trace 里，我们还看到了一条过长的同步链路：

1. `validate user`
2. `reserve inventory`
3. `persist order`
4. `enqueue terminalization`

在这条链路里，真正拖长用户请求尾部的，不只是 reservation 终态处理本身，还包括 `order-service` 仍然把“确认结果”当成请求路径的一部分去处理的倾向。
换句话说，0002 已经把“库存终态”拆出去了，但 `/orders` 这个入口本身还需要再瘦身一次。

本次 trace 的直接表现是：

- `user-service lookup` 约 1.2s
- `product-service reserve` 约 1.6s
- `order db create` 约 1.2s
- 同步确认尾段继续拉长请求

## 与 ADR 0002 的关系

可以把两份 ADR 看成同一条链路上的两次切分：

- ADR 0002 关注的是：reservation 的 `confirm / cancel` 不能再阻塞前台请求
- ADR 0002-1 关注的是：`POST /orders` 不应该再等到“最终确认语义”都落地之后才返回

它们复用的是同一套 `durable queue + worker` 思路，但切开的边界不同：

- 0002 切的是 reservation terminalization
- 0002-1 切的是 order create path 的同步确认尾段

## 决策

我们将 `POST /orders` 的确认步骤改成异步终态处理。

### 新流程

1. `order-service` 校验用户
2. `order-service` 预留库存
3. `order-service` 在同一个数据库边界内创建 `pending` 订单，并 durable 地写入 terminalization task
4. `POST /orders` 立即返回，不再等待 `confirm / cancel`
5. 后台 worker 处理 terminalization task
6. worker 在 `confirm` 成功后把订单推进到 `confirmed / succeeded`

### 具体实现

- `CreateOrderUseCase.create_order()` 不再同步调用 `finalize_order()`
- 新增 `create_order_and_enqueue_terminalization()`，把订单创建和 task 入队放进同一事务边界
- `ProcessTerminalizationTaskUseCase` 在 `confirm` 成功后回写订单状态
- `cancel` 类型 task 只负责终结 reservation，不再回写已经由其他路径决定好的订单终态

## 为什么这样能缩短 trace

修复前，`/orders` 路径必须等待：

- 用户校验
- 商品预留
- 订单写入
- 订单确认尾段
- 终态确认完成后的订单状态回写

修复后，`/orders` 只需要等待：

- 用户校验
- 商品预留
- 订单写入
- durable enqueue

也就是说，最重的同步尾段从请求路径里消失了。请求依然要等待 `user lookup` 和 `reserve inventory`，但不再背负终态处理的额外延迟。

## 影响

预期收益：

- `/orders` 的 p95/p99 延迟下降
- trace 链路更短，更容易区分“下单本身慢”与“后台终态处理慢”
- 终态处理失败可以通过 worker 重试，而不会阻塞前台请求
- 订单最终状态和 reservation 终态的边界更清晰

代价与权衡：

- `POST /orders` 的响应语义从“立即 confirmed”变成“先返回 pending，再由 worker 推进到 confirmed”
- 上层调用方如果关心最终状态，需要通过 `GET /orders/{id}` 或后续事件来观察
- worker 和 queue 的健康度变成必须关注的运营指标

## 观测与运维

这次改动后，Grafana 至少要关注三类信号：

- `event=order_service_create_order_timing`
- `event=order_service_enqueue_task`
- `event=order_service_terminalization_call`

如果 `order_service_create_order_timing` 下降，而 `order_service_terminalization_call` 仍然抖动，说明前台路径已经被切短，但后台终态消费还在落后。

## 时序图对比

这张图把 ADR 0002 和 ADR 0002-1 放在一起看。

- 左边是 ADR 0002 已经引入的异步 reservation terminalization
- 右边是 ADR 0002-1 在此基础上进一步把 `/orders` 的确认尾段移出请求路径
- 红色 box 表示改造前仍然留在同步路径里的部分
- 绿色 box 表示新增的 durable enqueue 边界
- 蓝色 box 表示已经从请求路径移到后台 worker 的部分

![ADR 0002 vs 0002-1 sequence diagram](./diagrams/0002-1-order-confirmation-comparison.svg)

## 相关变更

- `flashsale/order-service/app/application/create_order_use_case.py`
- `flashsale/order-service/app/application/process_terminalization_task_use_case.py`
- `flashsale/order-service/app/adapters/order_postgres_unit_of_work.py`
- `flashsale/order-service/app/entrypoints/http_api.py`
- `flashsale/order-service/tests/unit/test_order_lifecycle.py`
- `flashsale/order-service/tests/interagtion/order_compose_integration.py`
- `flashsale/scripts/integration_test_support.py`
