# 单元 D02：观察存储和写入批次

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 40 分钟，包含动手实验和测验 |

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
| D02-01：列式存储与查询路径 | 查询流程图 | 5 分钟 | 沿订单查询解释 FE、BE 和列式读取 |
| D02-02：存储层次与 Compaction | 层次图与例子 | 5 分钟 | 区分分片、写入版本和列式文件 |
| D02-03：写入批次与可见性 | 对照分析 | 5 分钟 | 说明事务次数不同但业务结果相同 |
| 实验 2 | 动手操作 | 20 分钟 | 完成两种写入并核对十行、12220.60 |
| 测验 2 | 交互测验 | 5 分钟 | 检查查询路径、存储层次和观测方法 |

## D02-01：列式存储与查询路径

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

## D02-02：Tablet、Rowset、Segment 与 Compaction

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
本课程沙箱使用单副本，不用于验证副本恢复。

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

这不是“先合并才能查询”的意思。写入事务发布为可见版本后即可供查询使用，
不必等后台合并结束。各写入接口何时返回成功、何时可见，要按接口语义理解。

## D02-03：写入批次与可见性

### 公平比较一次写十行和分十次写

Lab 使用 D01 的同一批 WWI 历史订单，不改金额或日期。

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

### 观察结果要怎么解释？

| 证据 | 可以回答 | 不能据此断言 |
| --- | --- | --- |
| 行数、明细、金额 | 两种写法是否保存相同业务数据 | 哪种写法生产吞吐更高 |
| EXPLAIN | 计划扫描什么、在哪里过滤 | 实际耗时与磁盘 IO |
| Tablet 元数据中的版本相关信息 | 采样时的存储状态 | Rowset/Segment 的完整清单 |
| Query Profile | 实际执行中的算子耗时、扫描统计等 | 脱离机器、缓存与数据规模的固定性能倍数 |

后台合并可能在采样前完成。因此，Lab 不要求 VersionCount 必然不同，
也不以十行数据的耗时排名。当前动手范围是小样本写入、查询计划和基础元数据，
不包含大数据 Profile 或底层 Rowset 管理接口实验。

## 动手实验 2：观察存储和写入批次

开始前请完成 D01，能够连接实验实例并核对订单总量，并使用课程独立实验库。

打开[实验 2](lab2_observe_storage.ipynb)，按顺序完成：

1. 创建相同结构的批量写入表和逐行写入表。
2. 用相同订单样本执行两种写入方式，观察 Tablet 元数据。
3. 核对两表均为十行、12220.60，记录采样时刻和后台合并的影响。

完成后保存查询结果与差异原因；未执行的步骤不要标记为完成。

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
能解释结果和选择原因，比只记住命令名称更重要。

## 官方参考资料

- [系统架构](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/)
- [分区与分桶基础](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)
- [Compaction 原理](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)
- [EXPLAIN](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-query/EXPLAIN/)
- [Query Profile](https://doris.apache.org/docs/4.x/query-acceleration/query-profile/)
- [Group Commit](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)

官方文档会随版本更新；本课程实验版本及已验证环境见课程信息和[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)。
