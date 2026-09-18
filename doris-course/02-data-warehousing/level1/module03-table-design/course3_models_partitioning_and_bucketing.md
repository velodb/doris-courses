# 单元 D03：Doris 表模型、分区与分桶

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 55 分钟，包含讲义阅读、动手实验和测验 |

[课程目录](../README.md) · [打开实验 3](lab3_models_and_pruning.ipynb) · [打开测验 3](quiz3_models_and_data_distribution.ipynb)

## 单元目标

本单元介绍 Doris 的三种表模型，以及分区和分桶的作用。

完成本单元后，你将能够比较重复键在不同模型中的处理结果，并通过 EXPLAIN 观察查询的扫描范围。

## 学习目标

完成本单元后，你应该能够：

1. 预测相同输入在 Duplicate、Unique、Aggregate 模型中的结果。
2. 按明细、当前状态或汇总指标的用途选择逻辑键和表模型。
3. 区分分区、分桶、排序键和业务唯一键的职责。
4. 通过 EXPLAIN 比较日期过滤和分桶键过滤的扫描范围。
5. 结合明细与金额核对结果，不把计划裁剪等同于性能提升。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| D03-01：Doris Key Model | 输入与结果对照 | 10 分钟 | 为明细、当前状态和汇总选择模型 |
| D03-02：分区、分桶与数据分布 | DDL 与查询计划 | 15 分钟 | 区分物理组织和逻辑唯一性 |
| 实验 3 | 动手操作 | 25 分钟 | 验证三种模型结果，比较三个过滤计划 |
| 测验 3 | 交互测验 | 5 分钟 | 检查模型选择、键和计划证据 |

## D03-01：Doris Key Model

建表时选择的 Key Model（表模型），决定键相同的数据如何处理。
原始接入表需要留下每次输入，订单当前表需要反映最新金额，销售汇总表需要累计指标。
这三种需求分别对应保留明细、按键更新和按函数聚合。先确定一行的业务含义，
再选模型，才能让写入行为与报表口径一致。

### 同样的输入，为什么结果不同？

WWI 订单 1 的金额是 2300.00，订单 2 是 405.00。现在在隔离实验表里，
把订单 1 的金额模拟修正为 2250.00。
按下表顺序分三次提交，并等每次写入完成：

| 写入顺序 | id | amount | 含义 |
| --- | ---: | ---: | --- |
| 1 | 1 | 2300.00 | 订单 1 的原金额 |
| 2 | 1 | 2250.00 | 订单 1 的修正金额 |
| 3 | 2 | 405.00 | 订单 2 的金额 |

**Duplicate Key：保留每条明细。** 键决定排序，不是唯一性约束。

| id | amount |
| ---: | ---: |
| 1 | 2250.00 |
| 1 | 2300.00 |
| 2 | 405.00 |

**Unique Key：保留每个键的当前值。** 在本例顺序提交、未设置 Sequence 列的条件下，
后一次写入替换同键的先前值；处理乱序业务版本时还要使用 D07 的版本规则。

| id | amount |
| ---: | ---: |
| 1 | 2250.00 |
| 2 | 405.00 |

**Aggregate Key：按声明的函数合并值。** 本例 amount 声明为 SUM：

| id | amount |
| ---: | ---: |
| 1 | 4550.00 |
| 2 | 405.00 |

4550.00 是 2300.00 + 2250.00。引擎正确执行了 SUM，
但它不是订单 1 的当前金额：两个快照不能当成两笔销售累加。

完成 Lab 3 后可以分别核对：

```sql
SELECT id, amount FROM orders_duplicate ORDER BY id, amount;
SELECT id, amount FROM orders_unique ORDER BY id;
SELECT id, amount FROM orders_aggregate ORDER BY id;
```

### 先问“一行表示什么”，再选模型

| 要保留的内容 | 逻辑标识 | 本课程采用的模型 |
| --- | --- | --- |
| 每次原始投递 | 投递编号 | Duplicate Key 保留每次记录 |
| 一笔订单的当前状态 | order_id | Unique Key，并在 D07 加业务版本 |
| 每个不同业务事件 | event_id | Unique Key，对相同内容的重投去重 |
| 按维度累计的可加指标 | 日期、商品等维度组合 | Aggregate Key，明确 SUM 等函数 |

当前表与历史表可以都用 Unique Key，但键不同、用途不同。
历史表用 order_id 会把同一订单的不同事件覆盖掉。
模型的具体语义见文末三种模型的官方资料。

## D03-02：分区、分桶与数据分布

### 四种设计不要混在一起

选好“相同订单如何处理”之后，还要决定“数据放在哪里”。例如每天都要查询订单日报，
可以先按日期分区，让昨天的查询集中读取昨天的数据；一天的数据再按订单号分桶，
分散到多个 Tablet，为并行处理提供分片。

| 设计 | 回答的问题 | 本节例子 |
| --- | --- | --- |
| 分区 | 哪些数据属于同一范围，哪些范围可以不读？ | 按 order_date 分两天 |
| 分桶 | 分区内数据如何分到 Tablet？ | HASH(order_id)，每个分区四桶 |
| 排序键 | 数据在存储中如何排序？ | order_date、order_id |
| 业务唯一键 | 什么标识同一个业务对象？ | 订单当前状态以 order_id 标识 |

Lab 的分区表使用 Duplicate Key 保留历史明细，日期和订单号用于排序。
另建订单当前表时，应重新核对业务唯一键：如果同一订单的日期可能被修正，
把日期也作为唯一键的一部分，就会把修正前后识别为两个不同的键。

下面是 Lab 创建的物理布局，供阅读；建表和初始化由 Lab 执行：

```text
orders_partitioned
├── p_day1：2013-01-01 ≤ order_date < 2013-01-02
│   └── HASH(order_id)，4 个 Tablet
└── p_day2：2013-01-02 ≤ order_date < 2013-01-03
    └── HASH(order_id)，4 个 Tablet
```

### 比较三个查询计划

Hash 分桶根据分桶列的值计算目标桶。同一分区内，相同订单号会落入同一个桶；
具体桶号由 Hash 计算，不能把订单号直接当桶号。查询同时给出日期和订单号等值条件时，
Doris 就有机会先锁定一天，再锁定该订单所在的桶。
分桶数决定分片数量，增加分桶也会增加管理开销，需要结合数据量选择。
相关规则见[分区与分桶](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)。

完成 Lab 初始化后执行：

```sql
EXPLAIN SELECT * FROM orders_partitioned;
EXPLAIN SELECT * FROM orders_partitioned WHERE order_date = '2013-01-01';
EXPLAIN SELECT * FROM orders_partitioned
WHERE order_date = '2013-01-01' AND order_id = 1;
```

| 查询 | 关注的变化 | 原因 |
| --- | --- | --- |
| 无过滤 | 两个分区及其 Tablet | 没有排除任何日期或订单 |
| 日期过滤 | 只需第一天的分区 | 第二天的数据不满足日期条件 |
| 日期＋订单号 | 第一天下进一步缩小 Tablet 范围 | Hash 键等值条件可用于分桶裁剪 |

在扫描节点中找所选分区和 Tablet 数量；具体字段名称随版本而变。
不要只截取一段计划就宣布查询加速：计划说明预期工作范围，
实际效果还受扫描量、缓存和计算开销影响。

### 裁剪不能改变答案

```sql
SELECT COUNT(*) AS sample_orders, SUM(amount) AS order_amount
FROM orders_partitioned
WHERE order_date = '2013-01-01';
```

结果应为五笔、3944.20。再加 order_id=1 时应回到订单 1 的 2300.00，
不是隔离模型实验中的修正值 2250.00；这两组表的数据用途不同。
本节只验证模型语义和计划裁剪，不用十笔样本比较生产性能。

## 动手实验 3：模型语义与分区分桶

开始前请完成 D01、D02，了解订单字段和基本存储结构，并使用课程独立实验库。

打开[实验 3](lab3_models_and_pruning.ipynb)，按顺序完成：

1. 向三种模型写入相同键的不同金额，核对逐行结果。
2. 创建独立的日期分区明细表，准备相同查询的不同过滤条件。
3. 比较无过滤、日期过滤、日期加订单号过滤的计划和结果。

### 数据来源与说明

实验使用 Microsoft WWI 官方模拟批发业务的历史子集，保留原始客户与商品标识。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- Duplicate 保留三行，Unique 保留两笔当前值，Aggregate SUM 得到订单 1 的 4550.00；相同输入不代表相同业务语义。
- 先确定一行的粒度和逻辑键，再选模型；order_id 维护当前状态，event_id 保留不同事件。
- 分区管范围，分桶管分布，排序键管顺序；这些物理设计不能悄悄改变业务唯一性。
- 日期条件与 Hash 键条件可以在不同层次裁剪；用 EXPLAIN 检查实际选择的分区和 Tablet。
- 核对明细与金额后再讨论效率；SUM 正确执行不代表指标口径正确，计划缩小也不等于已测得加速。

## 知识测验 3：模型语义与分区分桶

完成讲义和实验后，打开[测验 3](quiz3_models_and_data_distribution.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。

## 官方参考资料

- [Duplicate Key 明细模型](https://doris.apache.org/docs/4.x/table-design/data-model/duplicate/)
- [Unique Key 主键模型](https://doris.apache.org/docs/4.x/table-design/data-model/unique/)
- [Aggregate Key 聚合模型](https://doris.apache.org/docs/4.x/table-design/data-model/aggregate/)
- [分区与分桶基础](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)
- [数据分桶](https://doris.apache.org/docs/4.x/table-design/data-partitioning/data-bucketing/)
- [EXPLAIN](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-query/EXPLAIN/)
