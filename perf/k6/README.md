# Flashsale Perf 说明

这个目录放的是 `flashsale` 的性能测试基建，当前主要基于 `k6`。这里的设计目标不是只“发流量”，而是保证每次压测前后的数据边界清晰，方便把结果和 Grafana 面板对应起来看。

## 目录结构

- `lib/`
  - 放通用工具函数，比如发请求、构建 `k6` options、批量造用户/商品、统一 reset。
- `setup/`
  - 放压测前初始化逻辑。
  - 负责清理旧数据、按场景写入本轮压测需要的用户和商品。
- `teardown/`
  - 放压测后的收尾逻辑。
  - 负责在需要时先 drain 异步 terminalization，再统一清理数据。
- `scenarios/`
  - 放具体压测场景，比如并发测试、热点库存测试、幂等性相关测试。

## 执行模型

当前约定是：

1. `setup()` 先调用 `/admin/reset` 类接口，清掉 order / user / product 三个服务里的旧数据。
2. 再按本次场景需要，写入用户、商品、初始库存等测试数据。
3. `default()` 只关注发压测流量，不再夹杂初始化逻辑。
4. `teardown()` 先做正确性检查；如果订单终态是异步落库的，还会先触发 terminalization drain。
5. 最后再统一清理所有测试数据，保证下一次 perf run 从干净状态开始。

这个约束很重要，因为我们现在很多场景是反复跑的。如果上一次压测留下订单、库存、异步任务，本次的吞吐、延迟、backlog 都会被污染。

## setup / teardown 的职责边界

### setup

`setup/perf-run.js` 负责两类事情：

- 统一 reset 所有依赖服务，确保数据库没有历史残留。
- 根据场景批量创建用户和商品，返回给场景主流程使用。

热点路径场景还提供了 `setupSingleUserAndProductScenario()`：

- 只创建一个用户和一个商品。
- 所有流量都打到同一个商品库存上。
- 这样更容易稳定复现库存锁竞争、热点行竞争和尾延迟抖动。

### teardown

`teardown/perf-run.js` 负责两类事情：

- 如果场景依赖异步 terminalization，就先反复调用处理接口，尽量把排队中的终态任务清空。
- 然后统一 reset 服务数据，避免污染下一次压测。

这里先 drain 再 cleanup 的原因是：

- 如果直接 reset，Grafana 上能看到的 backlog、retry、processing 轨迹会被截断。
- 如果不 reset，下一次压测又会带着旧任务一起跑，结果不可比。

## 场景约定

### `concurrency-test.js`

这是主并发压测场景：

- `setup()` 会根据 `PROFILE` 批量创建用户和商品。
- `default()` 负责下单流量。
- `teardown()` 会先校验是否出现 oversell，再做 terminalization drain 和全量 cleanup。

其中：

- `seedlow` profile 是最低风险的预热档，适合先验证 OTEL / Kafka / dashboard 有没有数据，再决定要不要继续加压。
- `smoke` profile 是真正的非热点冒烟场景，会把流量分散到多个商品。
- `hotspot100` 和 `hotspot` profile 会故意把所有请求压到一个商品上，用来观察热点库存路径。

### `k6-hotspot-order-scenario.js`

这是热点库存场景的工厂，给不同强度的热点测试复用：

- 保持“一个用户 + 一个商品”的数据模型。
- 通过不同的 ramp、VU、threshold 组合复用同一条热点路径。

## 数据隔离原则

Perf 目录里的 setup/teardown 现在默认追求下面这件事：

- 每次 run 前数据库是干净的。
- 每次 run 后数据库也应该回到干净状态。

这意味着：

- 不应该把测试初始化逻辑散落在各个 scenario 文件里。
- 不应该依赖“上一次刚好留着的数据”。
- 不应该在 teardown 跳过 cleanup，除非你是为了临时排障并且明确知道后果。

## 如何看 Grafana

如果你是在跑 perf test，而不是线上真实流量，最有价值的是观察“时间序列上的变化”，不是只看最后一个数字：

- HTTP endpoint dashboard：看每个 endpoint 的 throughput、p50/p95/p99 latency、4xx/5xx。
- async terminalization dashboard：看 queued、processing、retry、success 的时间演化。

因此 setup / teardown 的目标之一，就是让这些时间序列能真实反映“一次完整压测”发生了什么。
