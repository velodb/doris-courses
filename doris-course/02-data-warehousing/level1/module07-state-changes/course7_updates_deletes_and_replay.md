# Module 7：数据更新、删除与事件重放

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 75 分钟，包含讲义阅读、动手实验和测验 |

[课程目录](../README.md) · [打开实验 7](lab7_current_state_and_replay.ipynb) · [打开测验 7](quiz7_state_changes_and_replay.ipynb)

## 单元目标

本单元介绍订单状态更新、重复与乱序事件处理，以及历史保留和重放的方法。

完成本单元后，你将能够通过模拟事件维护订单当前状态与历史，并核对中断后重放的结果。

## 学习目标

完成本单元后，你应该能够：

1. 区分订单当前状态、事件历史和原始投递的键与用途。
2. 使用业务版本解释重复与乱序事件的处理结果。
3. 区分部分列更新与省略字段的整行写入。
4. 区分软删除、SQL 删除与物理文件回收。
5. 通过重复重放、逐行比较和独立业务流水核对恢复结果。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| 7.1 当前状态与更新 | 表粒度对照 | 5 分钟 | 选择当前、历史和投递的键 |
| 7.2 幂等与乱序 | 事件时间线 | 10 分钟 | 解释晚到旧事件为何不覆盖新状态 |
| 7.3 Merge-on-Write 与部分列更新 | 更新前后对照 | 10 分钟 | 检查未提供的业务字段是否保留 |
| 7.4 软删除、SQL 删除与回收 | 可见性对照 | 5 分钟 | 区分隐藏、删除与物理回收 |
| 7.5 历史、重放与恢复 | 中断案例与对账 | 10 分钟 | 解释跨步骤中断后的恢复方法 |
| 实验 7 | 动手操作 | 30 分钟 | 核对 11 笔当前订单、18 条历史、19 次投递 |
| 测验 7 | 交互测验 | 5 分钟 | 检查状态、版本、部分更新、删除和恢复 |

## 7.1 当前状态与更新

### 业务想同时知道“现在怎样”和“发生过什么”

运营看板需要当前状态，排查退款则需要历史过程。
只保留当前状态会丢失过程，只保留历史又需要每次查询挑选正确版本。
本 Lab 把这两类需求分开：

| 表 | 一行表示什么 | 键与作用 |
| --- | --- | --- |
| orders_current | 一笔订单的当前状态 | UNIQUE KEY(order_id)，按 event_version 裁决 |
| order_events | 一个不同的业务事件 | UNIQUE KEY(event_id)，相同事件重投不增加逻辑历史 |
| event_deliveries | 某次尝试中的一次投递 | 记录 attempt_id、delivery_id 和原始内容，保留重投 |

同一个 order_id 可以有多个 event_id，同一个 event_id 又可能被投递多次。
这里的 delivery_id 是投递编号，不是快递单号。

### 从合格新订单建立当前表和历史表

Module 6 的 `orders_clean` 提供十笔合格模拟订单；Module 7 将其初始化为
当前状态与初始历史。来源均为 COURSE_SIMULATION，不更新 `wwi_*` 历史表。

Unique Key 的更新改变同键的逻辑当前值，不意味着旧物理文件马上被回收。
当前表使用 Merge-on-Write（MoW），在写入侧处理同键版本的可见性，
便于业务查询读取当前结果。

## 7.2 幂等与乱序

### 业务顺序不等于到达顺序

订单 900001 的业务流程是：

```text
版本 1 CREATED → 版本 2 PAID → 版本 3 SHIPPED → 版本 4 DELIVERED
```

但本 Lab 中，版本 1 已初始化，后续事件的到达顺序故意打乱：

| 首轮投递位置 | event_id | event_version | 事件状态 | 处理后当前状态 |
| --- | --- | ---: | --- | --- |
| 1 | E08 | 4 | DELIVERED | DELIVERED，版本 4 |
| 2 | E02 | 3 | SHIPPED | 仍是 DELIVERED，版本 4 |
| 3 | E01 | 2 | PAID | 仍是 DELIVERED，版本 4 |
| 9 | E02 | 3 | SHIPPED，重复投递 | 仍是 DELIVERED，版本 4 |

这些事件携带完整的更新后状态（after-image），不是只包含变化字段的补丁。
当前表将 `event_version` 配置为 Sequence 列，按同订单的业务版本比较，
不是按最后到达的消息覆盖。历史表则仍保留三个不同的事件。

完整的更新后状态意味着：版本 4 除了 DELIVERED，还携带当时的金额、客户、支付等字段。
即使版本 2、3 尚未到达，版本 4 也足以建立当前行。若消息只包含某个变化字段，
则需要按部分更新的语义处理，不能直接套用整行状态的写入方式。

Lab 在第一次写入之前展示实际 DDL。先找到下面的键与属性片段：

```text
UNIQUE KEY(order_id)
"enable_unique_key_merge_on_write"="true"
"function_column.sequence_col"="event_version"
```

第一项识别订单，第二项启用写时合并，第三项指定业务版本列。
仅把普通字段命名为 event_version 不会启用版本裁决。
创建订单当前表时，将这些配置写入建表语句。下例使用该配置，并在每次写入时提供完整订单状态。

Sequence 列是 Doris 比较同一 Key 下记录新旧的依据。
对订单 900001，已有版本 4 时，后到的版本 2 不会让当前状态退回 PAID。
这要求上游为同一订单提供可比较、能表达业务先后的版本；
不同订单之间无需比较版本大小，同版本冲突则需要额外约定处理规则。

完成 Lab 后，在同一实验库核对：

```sql
SELECT order_id, status, event_version, paid_amount, refund_amount
FROM orders_current
WHERE order_id IN (900001, 900003)
ORDER BY order_id;
```

| order_id | status | event_version | paid_amount | refund_amount |
| ---: | --- | ---: | ---: | ---: |
| 900001 | DELIVERED | 4 | 100.00 | 0.00 |
| 900003 | REFUNDED | 4 | 150.00 | 150.00 |

### 什么才是安全的重复？

幂等的意思是重复处理同一件事，不改变已经正确的业务结果。
本课要求同 event_id 的重投携带相同业务内容；同 ID 不同内容是冲突，
不能用“重复了就覆盖”解释。

event_version 是每笔订单单调递增的教学版本，不是 Kafka offset 或 Binlog 位点。
同版本不同内容需要源端定义冲突规则，本 Lab 不验证这种冲突的自动解决。
具体 Sequence 列行为见[并发更新控制](https://doris.apache.org/docs/4.x/data-operate/update/unique-update-concurrent-control/)。

## 7.3 Merge-on-Write 与部分列更新

Merge-on-Write（写时合并）在写入阶段处理同键新旧版本的可见性。
例如订单从 CREATED 更新为 PAID，新状态可见后，普通查询直接读取新的逻辑行；
旧版本的数据文件由后台合并逐步整理。查询订单当前状态时，应用可以直接按订单号查询。

部分列更新解决的是另一件事：一次修改只提供部分字段时，
Doris 在符合条件的 Unique Key 表上保留未修改字段，将本次提供的值合入当前行。

### 只给三个字段，其他字段应该怎样？

假设只需要取消订单 900001，不希望重发整条订单。
“未提供字段”究竟表示保留旧值，还是按默认/空值等规则形成新行，
取决于采用的更新方式，不能只看 INSERT 里少写了几列。

Lab 在独立的 `orders_partial_update` 上演示，不改变主线当前表：

| 阶段 | status | event_version | order_amount | region |
| --- | --- | ---: | ---: | --- |
| 初始行 | CREATED | 1 | 100.00 | EAST |
| 部分更新只提交状态与版本 | CANCELLED | 2 | 100.00 | EAST |

本步骤临时开启 `enable_unique_key_partial_update`，写入订单号、状态和新版本，
完成后恢复会话设置。订单号定位当前行，新版本参与新旧裁决，状态是本次要修改的内容；
金额与地区由已有行保留。使用这种方式前，应明确启用部分更新以及目标表的支持条件。

```sql
SELECT order_id, status, event_version, order_amount, region
FROM orders_partial_update
ORDER BY order_id;
```

结果应与表中第二行一致。只看到 CANCELLED 还不够，金额和地区必须未被清空。
适用表模型和配置条件见[部分列更新](https://doris.apache.org/docs/4.x/data-operate/update/partial-column-update/)。

## 7.4 软删除、SQL 删除与回收

### 三种“删除”回答不同的问题

| 操作 | 查询结果 | 数据含义 |
| --- | --- | --- |
| 设置业务字段 is_deleted=true | 普通 SELECT 仍能看到，需要显式过滤 | 应用决定不再展示 |
| SQL DELETE | 普通查询不再返回被删除的行 | 数据库层改变逻辑可见性 |
| 后台物理回收 | 不是业务查询条件 | 存储文件在符合回收条件后释放 |

Lab 的独立副本开始有两行：900001、900002。
先软删除 900001，表里仍有两行，但 `WHERE is_deleted=false` 只返回 900002。
再用 SQL DELETE 删除 900002，普通查询只剩软删除标记为真的 900001。

```sql
SELECT order_id, is_deleted, status
FROM orders_delete_demo
ORDER BY order_id;
```

最终查询结果为 900001、true、CREATED。磁盘空间由后台机制在满足回收条件后释放。
业务软删除字段、导入删除标记和内部 Delete Bitmap 不是同一层机制；
本 Lab 只执行软删除和 SQL DELETE。

退款不是删除：主线中的 900003 应保留支付与退款金额，状态为 REFUNDED，
不能为了让净收款变成零就删除支付事实。

## 7.5 历史、重放与恢复

### 中断可能发生在两个写入之间

本 Lab 通过停止一个课程步骤模拟中断，不终止数据库进程：

```text
记录首次投递 → 写入历史 → [模拟中断] → 尚未写入当前表
                                          │
                 重新投递整批事件 ←────────┘
                      │
                      ├─ 原始投递继续记录
                      ├─ 历史按 event_id 去重
                      └─ 当前状态按 order_id + 版本裁决
```

这些表由代码分步维护，不假设三张表自动原子提交。
恢复不能只检查“历史里已经有 event_id”，否则可能跳过尚未完成的当前状态写入。

### 为什么三个行数不同？

| 检查对象 | 预期数量 | 推导 |
| --- | ---: | --- |
| 当前订单 | 11 | 十笔初始订单＋一笔新订单 |
| 逻辑历史 | 18 | 十条初始快照＋八个不同事件 |
| 原始投递 | 19 | 中断前一次＋两轮各九次投递 |

重复投递应增加投递记录，不应增加不同事件数或改变正确的当前状态。

```sql
SELECT 'current' AS record_type, COUNT(*) AS rows_count FROM orders_current
UNION ALL
SELECT 'history' AS record_type, COUNT(*) AS rows_count FROM order_events
UNION ALL
SELECT 'deliveries' AS record_type, COUNT(*) AS rows_count FROM event_deliveries
ORDER BY record_type;
```

### 用另一套业务事实核对金额

使用 `business_events.json` 中单独记录的商品明细、支付、退款和配送流水核对订单当前状态。
退款应关联原支付，配送应关联订单，客户和商品应能在 Module 5 导入的 WWI 维度中找到。

```sql
SELECT SUM(order_amount) AS orders_amount,
       SUM(paid_amount) AS paid,
       SUM(refund_amount) AS refunded,
       SUM(paid_amount - refund_amount) AS net_receipts
FROM orders_current;
```

预期依次为 1510.00、250.00、150.00、100.00。
订单 900003 累计支付仍是 150.00，累计退款也是 150.00，净收款才是零。
这些是教学模拟业务，与 WWI 原始账户收款分开。

本实验通过文件中的模拟事件练习应用步骤中断后的重放。
将这种做法用于 CDC 管道时，还需要结合源端日志位置、快照切换和连接器恢复机制，
确保恢复时能重新取得所需事件。

## 动手实验 7：乱序裁决、历史保留和可恢复重放

开始前请先完成 Module 6，并使用同一课程独立实验库；本实验会读取其中的 orders_clean 合格订单表。

打开[实验 7](lab7_current_state_and_replay.ipynb)，先在独立表观察正常更新、相同事件重复和旧版本迟到，再进入完整订单流程：

1. 从 Module 6 的合格订单初始化当前表、历史表和投递表。
2. 模拟一次步骤中断，再按指定顺序重投事件并完整重放。
3. 核对当前订单 11 行、逻辑历史 18 行，以及订单状态和金额。
4. 用独立业务流水核对金额和客户、商品、事件关联。
5. 在独立副本中执行部分列更新、软删除和 SQL DELETE，再检查主线数据。

### 数据来源与说明

历史部分采用 Microsoft WWI；新订单及变更标为 COURSE_SIMULATION，引用 WWI 客户和商品，但不回填历史。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- 当前表按 order_id，事件历史按 event_id，投递记录按每次尝试保存；三个粒度不能混用。
- Sequence 列用业务版本裁决乱序，重投要求内容一致；到达顺序和日志位点不是业务新旧顺序。
- 部分列更新需要相应模型与配置，既检查新状态，也检查未提供字段；实验结束恢复会话设置。
- 软删除、SQL DELETE 和物理回收不同；退款要保留业务事实，不用删除订单代替退款。
- 恢复后核对 11/18/19 三种行数、完整记录和独立业务流水；支付 250.00、退款 150.00、净收款 100.00。

## 知识测验 7：乱序裁决、历史保留和可恢复重放

完成讲义和实验后，打开[测验 7](quiz7_state_changes_and_replay.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。

## 官方参考资料

- [Unique Key 主键模型](https://doris.apache.org/docs/4.x/table-design/data-model/unique/)
- [Sequence 列与并发更新控制](https://doris.apache.org/docs/4.x/data-operate/update/unique-update-concurrent-control/)
- [部分列更新](https://doris.apache.org/docs/4.x/data-operate/update/partial-column-update/)
- [DELETE](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-modification/DML/DELETE/)
- [Compaction 原理](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)
