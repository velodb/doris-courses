# Module 2：Doris 存储架构与写入机制

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 60 分钟，包含讲义阅读、动手实验和测验 |

[课程目录](../README.md) · [打开实验 2](lab2_observe_storage.ipynb) · [打开测验 2](quiz2_storage_and_write_batches.ipynb)

## 单元目标

本单元介绍 Doris 的查询路径、存储结构，以及不同写入批次与后台合并的关系。

完成本单元后，你将能够比较批量与逐行写入的结果，并观察 Tablet 等存储元数据。

## 学习目标

完成本单元后，你应该能够：

1. 沿一条查询说明 FE 规划与 BE 执行的分工。
2. 解释 Table、Partition、Tablet、Rowset 和 Segment 的层次。
3. 说明写入批次、数据可见性与后台 Compaction 的关系。
4. 控制表结构和数据，比较批量与逐行写入的结果。
5. 区分查询计划、存储元数据和运行时证据各能说明什么。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| 2.1 列式存储与查询路径 | 查询流程图 | 5 分钟 | 沿订单查询解释 FE、BE 和列式读取 |
| 2.2 Tablet、Rowset、Segment 与 Compaction | 层次图与例子 | 8 分钟 | 区分分片、写入版本和列式文件 |
| 2.3 写入批次与可见性 | 对照分析与状态读取 | 12 分钟 | 说明事务次数不同但业务结果相同 |
| 实验 2 | 动手操作 | 30 分钟 | 完成两种写入并核对十行、12220.60 |
| 测验 2 | 交互测验 | 5 分钟 | 检查查询路径、存储层次和观测方法 |

## 2.1 列式存储与查询路径

### 从一条查询看组件分工

假设分析师只想看订单 1 的金额，而不是取回整张订单表。完成 Lab 2 后，
下面的只读示例可以直接在同一实验库执行：

```sql
SELECT order_id, order_amount
FROM orders_batch
WHERE order_id = 1;
```

预期为一行：订单 1，税前金额 2300.00。客户端看到的是一条 SQL，
系统内部却要完成解析、规划、读取和计算。

```text
Notebook / SQL 客户端
        │ SQL
        ▼
FE：解析字段 → 优化查询 → 生成并分发执行计划
        │ 计划片段
        ▼
BE：扫描需要的列 → 过滤订单号 → 返回金额
        │
        ▼
客户端：展示结果
```

FE 决定要执行什么工作，BE 执行分配到的工作。大查询还可能在多个 BE 上
分别扫描和聚合，再合并结果；单节点实验只帮助理解分工，不演示多节点扩展。

### 为什么分析通常只读部分列？

订单表还包含客户、日期、明细数和来源，但这条查询只需要订单号和金额。
列式存储把同一列的值组织在一起，便于只读取查询所需的列。
列裁剪回答“读哪些列”，过滤和数据跳过回答“处理哪些行或数据块”，两者不同。

```sql
EXPLAIN SELECT order_id, order_amount
FROM orders_batch
WHERE order_id = 1;
```

在计划中找扫描表、输出列和过滤条件。EXPLAIN 展示计划，不等于执行过查询，
也不提供这次查询实际读取的字节数。运行时工作量要结合 Query Profile。

## 2.2 Tablet、Rowset、Segment 与 Compaction

订单到达 Doris 后，先按分区、分桶规则找到负责保存它的分片，再写成列式文件。
阅读存储术语时，可以沿着这个过程理解：Tablet 回答“归哪个分片”，Rowset 回答
“这一批写入形成了哪组数据”，Segment 回答“数据最终存在哪些文件里”。

### 先看每一层负责什么

```text
Table：SQL 中的一张表
└── Partition：按范围等规则组织数据
    └── Bucket / Tablet：分桶规则对应的数据分片
        └── Rowset：一次写入或合并形成的版本化数据集合
            └── Segment：不可变的列式数据文件
```

| 层次 | 用订单表理解 | 不应混淆的概念 |
| --- | --- | --- |
| Partition | 按订单日期划分数据范围；未显式分区也有默认分区 | 不是某个 BE 节点 |
| Bucket / Tablet | 分桶把分区内数据分到多个分片，Tablet 是对应的物理分片 | 桶数不是副本数 |
| Rowset | 写入涉及某个 Tablet 时，在该 Tablet 内形成版本化的数据集合 | 不是全表共享的一个文件 |
| Segment | Rowset 中保存列数据的文件，一个 Rowset 可以有多个 Segment | 不是一笔业务订单 |

例如，两天各一个分区，每个分区四个桶，得到八个 Tablet；
若再配置副本，会增加物理副本，不会把逻辑订单复制成多笔。
本课程沙箱使用单副本，便于观察一份数据的写入过程。

以 Lab 的单桶订单表为例：一次提交十笔订单，这些数据都写入同一个 Tablet；
分十次提交时，同一个 Tablet 会陆续接收十批数据。每批写入形成自己的 Rowset，
其中的列数据存放在 Segment 中。一个大批次可以生成多个 Segment，
因此业务行数、提交次数和文件数需要分别理解。

### 写入后为什么还要合并？

连续小批写入会形成多个数据片段。读取同一 Tablet 时，需要处理这些片段；
Compaction 在后台将多个 Rowset 合并，减少读取时需要处理的片段数量。
**合并改变物理组织，不应改变逻辑查询结果。**

```text
一个 Tablet 内：
写入 A → Rowset A ┐
写入 B → Rowset B ├─ Compaction → 合并后的 Rowset
写入 C → Rowset C ┘
```

写入事务发布为可见版本后，查询就能读取新数据。后台合并在此后整理文件：
即使三批数据仍分散在三个 Rowset 中，查询也能得到完整的订单汇总。
所以“业务结果何时可见”和“文件何时合并”是两个独立问题。
这也是排查写入问题时先检查事务结果、再检查 Compaction 的原因。
存储层的合并过程见[Compaction 原理](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)。

## 2.3 写入批次与可见性

### 公平比较一次写十行和分十次写

Lab 使用 Module 1 的同一批 WWI 历史订单，不改金额或日期。

| 对照项 | orders_batch | orders_rowwise |
| --- | --- | --- |
| 数据与字段 | 相同十笔订单 | 相同十笔订单 |
| 分桶与副本 | 一个桶、单副本 | 一个桶、单副本 |
| 写入方式 | 一批十行 | 每次一行，共十次 |
| Group Commit | off_mode | off_mode |
| 最终业务结果 | 十行，12220.60 | 十行，12220.60 |

关闭 Group Commit 是为了不让服务端攒批掩盖写入方式的差异。
它不是所有生产导入的推荐设置。

完成两种写入后，先检查业务结果，再看元数据：

```sql
SELECT 'batch' AS write_mode, COUNT(*) AS orders, SUM(order_amount) AS amount
FROM orders_batch
UNION ALL
SELECT 'small' AS write_mode, COUNT(*) AS orders, SUM(order_amount) AS amount
FROM orders_rowwise
ORDER BY write_mode;
```

```sql
SHOW PARTITIONS FROM orders_batch;
SHOW TABLETS FROM orders_batch;
SHOW TABLETS FROM orders_rowwise;
```

### 从 Tablet 信息读到 Rowset

先执行上面的 SHOW TABLETS，找到每张表的 TabletId、Version 和 VersionCount：

| 字段 | 怎么读 | 不代表什么 |
| --- | --- | --- |
| TabletId | 用它继续定位这个分片 | 不是业务订单号 |
| Version | 该副本报告的数据版本位置 | 不是当前文件个数，Compaction 不会把它重置为 1 |
| VersionCount | 采样时报告的版本数量，与合并状态有关 | 不等于提交次数，也不是 Segment 文件数 |

例如，某次采样中逐行表的 VersionCount 比批量表大，可以继续查看它是否保留了更多
未合并的 Rowset；不能仅凭这个数字断言查询慢了多少。元数据上报和后台合并都有时序，
记录采样时间，不要求每次运行得到相同的数量。

以下是命令形状，尖括号必须替换为本次返回值：

```text
SHOW TABLET <TabletId>;
执行返回的 DetailCmd（SHOW PROC ...）
读取返回的 CompactionStatus 地址，查看 rowsets
```

Rowset 清单中的版本范围用于理解哪些批次已经合并，例如 `[2-4]` 表示覆盖这段版本，
不是三笔订单。这只是读法示意，不是本实验固定输出。
课程容器返回的地址可能使用容器内网 IP；在宿主机查看时，只将本课程 BE 的地址换成
`http://127.0.0.1:51040`，保留 `/api/compaction/show?tablet_id=...` 路径。
这里只读取状态，不触发 Compaction，也不修改存储文件。
[Tablet 状态入口](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/tablet-local-debug/)

### 怎么取得一次真实查询的 Profile？

完成 Lab 2 后，在同一个 Notebook 的临时代码格执行以下观察代码。
它只打开本会话的 Profile 采集，不重建表，结束后恢复原设置：

```python
previous_profile = lab.query("SELECT @@enable_profile")[0][0]
try:
    lab.execute("SET enable_profile = true")
    lab.sql("SELECT order_id, order_amount FROM orders_batch WHERE order_id = 1")
    lab.sql("SHOW QUERY PROFILE")
finally:
    lab.execute("SET enable_profile = %s", (previous_profile,))
```

按数据库名、SQL 和开始时间找到刚才的查询，不要拿别人的查询做对照。
Profile 可能稍后收集完成；再查看列表，或在本课 FE Web UI 的 QueryProfile 页面打开详情。
先找扫描算子的行数、读取字节和耗时，再看过滤后输出：本查询最终返回一行，
不意味着底层只读了一行。比较列裁剪时，使用相同过滤条件，只改变 SELECT 的列。
本节不要求调优；完整慢查询分析放在 Level 2 的 Module 10。
[Profile 配置与查看](https://doris.apache.org/docs/4.x/query-acceleration/query-profile/)

### 观察结果要怎么解释？

| 观察项 | 主要用途 | 使用时关注 |
| --- | --- | --- |
| 行数、明细、金额 | 核对两种写法保存的业务数据 | 先确认结果一致，再比较写入吞吐 |
| EXPLAIN | 查看计划扫描范围与过滤条件 | 查询耗时和磁盘 IO 需要结合运行时统计 |
| Tablet 元数据中的版本相关信息 | 查看采样时的存储状态 | Rowset/Segment 的完整清单需要进一步查看存储信息 |
| Query Profile | 查看实际执行中的算子耗时、扫描统计等 | 比较性能时保持机器、缓存与数据规模等条件一致 |

后台合并可能在采样前完成。如果两张表的版本相关信息相近，先确认写入方式，
再结合采样时刻理解结果；一次采样反映的是当时的存储状态。
本节用十行数据理解写入机制。评估生产吞吐时，还需要固定数据规模、并发和机器资源，
持续观察写入延迟、版本积累及合并是否跟得上写入速度。

## 动手实验 2：观察存储和写入批次

开始前请完成 Module 1，能够连接实验实例并核对订单总量，并使用课程独立实验库。

打开[实验 2](lab2_observe_storage.ipynb)，按顺序完成：

1. 创建相同结构的批量写入表和逐行写入表。
2. 用相同订单样本执行两种写入方式，观察 Tablet 元数据。
3. 核对两表均为十行、12220.60，记录采样时刻和后台合并的影响。

### 数据来源与说明

实验使用 Microsoft WWI 官方模拟批发业务的历史子集，保留原始客户与商品标识。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- 客户端提交 SQL，FE 规划并分发任务，BE 读取列数据并执行过滤、聚合等运算。
- Table、Partition、Tablet、Rowset、Segment 是不同层次：表、范围、分片、版本集合和列式文件。
- 写入可见性取决于事务发布和接口语义；Compaction 在后台改善物理组织，不应改变业务结果。
- 比较批次时保持数据、表结构和配置一致；本实验两张表都必须是十行、12220.60。
- 计划、元数据和运行时统计各有用途；小样本和一次采样不能证明固定性能收益。

## 知识测验 2：观察存储和写入批次

完成讲义和实验后，打开[测验 2](quiz2_storage_and_write_batches.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。

## 官方参考资料

- [系统架构](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/)
- [分区与分桶基础](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)
- [Compaction 原理](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)
- [EXPLAIN](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-query/EXPLAIN/)
- [Query Profile](https://doris.apache.org/docs/4.x/query-acceleration/query-profile/)
- [Group Commit](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)
