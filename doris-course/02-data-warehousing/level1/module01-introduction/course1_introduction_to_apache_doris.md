# 单元 D01：认识 Doris，完成第一批订单分析

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 50 分钟，包含讲义阅读、动手实验和测验 |

[课程目录](../README.md) · [打开实验 1](lab1_connect_and_query.ipynb) · [打开测验 1](quiz1_doris_fundamentals.ipynb)

## 单元目标

认识 Apache Doris，并用十笔订单完成第一次分析：查看订单、筛选金额、按天汇总。
本节先学会使用，存储原理和表设计在后续单元展开。

## 学习目标

完成本单元后，你应该能够：

1. 区分“完成一笔交易”和“分析一批订单”这两类工作。
2. 说明 Doris 在业务数据库与分析用户之间承担什么职责。
3. 说明存算一体环境中 Frontend（FE）和 Backend（BE）的分工。
4. 连接 Doris，执行建表、写入和分组聚合 SQL。
5. 用明细、行数和金额核对数据，区分税前订单金额与收款。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| 1.1 什么是 Apache Doris？ | 场景与查询流程 | 5 分钟 | 区分交易与分析，说明 FE/BE 分工 |
| 1.2 为什么用 Doris 构建订单数仓？ | 业务需求与能力 | 5 分钟 | 说明 Doris 如何支持订单分析 |
| 1.3 基础 SQL 与订单分析 | SQL 示例与结果 | 10 分钟 | 看懂订单表，完成筛选和每日汇总 |
| 实验 1 | 动手操作 | 25 分钟 | 写入十笔订单样本并查询每日汇总结果 |
| 测验 1 | 交互测验 | 5 分钟 | 检查产品定位、组件分工和第一条分析 SQL |

前三项为讲义阅读与推演时间。首次下载实验镜像所需时间另计。

## 1.1 什么是 Apache Doris？

Apache Doris 是开源的实时分析数据库，可以用 SQL 查询明细、关联数据和计算汇总。
本课程用它回答一个简单的问题：**每天有多少笔订单，订单金额是多少？**
[产品介绍](https://doris.apache.org/docs/4.x/getting-started/what-is-apache-doris/)

### 交易与分析有什么不同？

| 工作 | 例子 | 关注点 |
| --- | --- | --- |
| 交易处理（OLTP） | 客户提交一笔订单 | 这笔订单是否正确保存 |
| 分析处理（OLAP） | 统计每天的订单数和金额 | 一批订单的整体情况 |

两类工作都可能使用 SELECT。查询订单地址供客户修改，是服务一笔交易；
按日期统计订单金额，才是这里要做的分析。

### FE 和 BE 各做什么？

本课的单容器沙箱中有一个 FE 和一个 BE，采用存算一体方式：BE 既存数据，也做计算。

| 组件 | 主要职责 |
| --- | --- |
| Frontend（FE） | 接收 SQL，管理表定义等元数据，规划和协调查询 |
| Backend（BE） | 保存内部表数据，执行扫描、过滤和聚合 |

可以把一次查询理解为：**客户端提交 SQL → FE 安排工作 → BE 读取和计算 → 返回结果。**
先记住这个分工即可，更细的查询和存储过程在 D02 学习。

## 1.2 为什么用 Doris 构建订单数仓？

在本课程中，业务系统负责处理下单等交易，Doris 接收订单数据，供分析师和报表查询。
“订单数仓”就是把分析需要的订单等数据整理到一起，方便持续查询和统计。

```text
业务系统中的订单 → 数据导入 → Doris → SQL 分析 / 业务报表
```

以订单分析为例，Doris 可以帮助你完成：

- **查询明细**：找到某一笔订单，查看客户、日期和金额。
- **汇总数据**：按日期或客户统计订单数量、总金额。
- **关联分析**：把订单与客户、商品等数据放在一起查询。

本节只用一张订单表完成前两项。外部数据查询、导入、质量检查和状态更新，
将在 D04–D07 逐步学习，不需要现在掌握全部功能。

Doris 支持 MySQL 兼容协议，本课通过 Notebook 中的 Python 工具连接并提交 SQL。
协议兼容不代表所有功能与 MySQL 相同；建立连接也不会自动同步业务数据，仍需导入或同步任务。

## 1.3 基础 SQL 与订单分析

### 先认识订单表

本课使用 Microsoft Wide World Importers（WWI）模拟批发业务中的十笔历史订单。
样本已经整理成 `orders_sample`，**一行代表一笔订单**，字段如下：

| 字段 | 含义 |
| --- | --- |
| order_id | 订单号 |
| customer_id | 客户号 |
| order_date | 订单日期 |
| order_amount | 订单中各商品的数量 × 单价合计，即税前订单金额 |
| line_count | 这笔订单包含的商品明细行数 |
| data_source | 数据来源，本节为 WWI |

一笔订单可以包含多条商品明细，但在这张表中只占一行。
这里的金额不等于已经收到的款项；是否付款，需要另外查看支付或账款数据。

### 看懂 Lab 中的建表语句

Lab 会提供并执行完整的 CREATE TABLE 和 INSERT。阅读时先认识这几项：

| 写法 | 在本实验中的含义 |
| --- | --- |
| BIGINT、DATE、DECIMAL(18,2) | 分别保存整数、日期和保留两位小数的金额 |
| NOT NULL | 该字段不允许为空 |
| DUPLICATE KEY(order_id) | 保留每次写入的记录，相同订单号不会自动去重 |
| BUCKETS 1、replication_num=1 | 使用一个桶、一份副本，适配本课单 BE 沙箱 |

模型、分区和分桶的详细设计留到 D03。完成建表后，可以用下面的命令查看表定义：

```sql
SHOW CREATE TABLE orders_sample;
```

**以下查询都在 Lab 完成建表和十行写入后执行。** 阅读讲义时先理解 SQL 和预期结果，
动手时使用 Lab 的同一个实验库。

### 第一步：查看一笔订单

```sql
SELECT order_id, customer_id, order_date, order_amount, line_count
FROM orders_sample
WHERE order_id = 4;
```

FROM 指定表，WHERE 选择订单 4，SELECT 指定要看的字段。预期返回一行：

| order_id | customer_id | order_date | order_amount | line_count |
| --- | --- | --- | --- | --- |
| 4 | 57 | 2013-01-01 | 445.20 | 3 |

意思是：客户 57 在 2013-01-01 下了这笔订单，包含三条商品明细，税前金额为 445.20。

### 第二步：筛选金额较大的订单

找出金额至少为 1000 的订单：

```sql
SELECT order_id, order_date, order_amount
FROM orders_sample
WHERE order_amount >= 1000.00
ORDER BY order_id;
```

`>=` 表示大于或等于，ORDER BY 按订单号排列结果。预期有三笔：

| order_id | order_date | order_amount |
| --- | --- | --- |
| 1 | 2013-01-01 | 2300.00 |
| 80 | 2013-01-02 | 1138.00 |
| 83 | 2013-01-02 | 6220.40 |

三笔合计 9658.40。Lab 的独立练习会让你自己完成这个筛选。

### 第三步：按天统计订单数和金额

```sql
SELECT order_date,
       COUNT(*) AS sample_orders,
       SUM(order_amount) AS order_amount
FROM orders_sample
GROUP BY order_date
ORDER BY order_date;
```

这次结果从“一行一笔订单”变成“一行一个日期”：

- GROUP BY 把同一天的订单放在一组。
- COUNT(*) 数这一组有几行；本表一行一单，因此得到订单数。
- SUM 将这一组的订单金额相加，AS 为结果列起名。
- ORDER BY 将日期排好顺序；它只负责排序，不负责汇总。

| 日期 | 样本订单数 | 税前订单金额 |
| --- | ---: | ---: |
| 2013-01-01 | 5 | 3944.20 |
| 2013-01-02 | 5 | 8276.40 |
| 合计 | 10 | 12220.60 |

SQL 返回两行日期汇总，“合计”行是人工核对用的。到这里，就回答了本节开头的问题。

### 最后：核对结果

```sql
SELECT COUNT(*) AS order_count,
       SUM(order_amount) AS total_amount
FROM orders_sample;
```

正常初始化后，应为 **10 行、12220.60**。再对照样本检查订单明细：
总额相同不代表每笔都正确，例如两笔金额一增一减就可能互相抵消。Lab 会同时检查总量和完整记录。

注意：不要单独重复执行十行 INSERT。本表会继续追加，变成二十行，金额变为 24441.20。
需要重做时，先确认只有可重建的教学数据，再按 Lab 的“建表 → 写入 → 查询”顺序运行；
建表步骤会重置 orders_sample。重试与去重在后续单元学习。

描述结论时记得限定范围：**这是十笔样本的税前订单金额，不是全部历史订单金额，也不是收款金额。**

## 动手实验 1：连接 Doris，查询第一批订单

打开[实验 1](lab1_connect_and_query.ipynb)，依次完成：

1. 加载实验工具，看到“实验工具加载完成”的提示。
2. 启动课程单容器沙箱并连接实验库，确认本节只重建 orders_sample。
3. 检查 FE/BE，确认当前实验数据库。
4. 阅读并执行完整建表 SQL 和十行 INSERT。
5. 核对明细和日期汇总，独立完成金额筛选练习。

讲义中的查询可用于解释和复习；建表、写入和重置操作集中在 Lab 中完成。

### 数据来源与说明

实验使用随课程提供的 WWI 十笔订单样本，来自[微软官方发布](https://github.com/microsoft/sql-server-samples/releases/tag/wide-world-importers-v1.0)，保留 MIT 许可。
字段、业务口径与后续变更样本见[数据说明](../../datasets/README.md)。

## 单元总结

- 交易处理完成一笔业务操作，分析处理统计一批数据。
- 本课程中，业务系统负责交易，Doris 接收数据并提供分析查询。
- FE 规划和协调查询，BE 保存数据并执行计算。
- 从建表、写入开始，用 WHERE 筛选、GROUP BY 汇总、ORDER BY 排序。
- 十笔样本的税前金额为 12220.60；核对总量后还要检查明细，不能把订单金额当作收款。

## 知识测验 1：Doris 基础与第一批订单

完成讲义和实验后，打开[测验 1](quiz1_doris_fundamentals.ipynb)。
五道单选题涵盖交易与分析、Doris 定位、FE/BE 分工、分组查询和金额口径；无需连接 Doris。
提交后阅读解释，再检查自己能否说明其他选项为什么不合适。

下一单元：[D02：观察存储与写入批次](../module02-architecture/course2_doris_architecture.md)。

## 官方参考资料

- [Apache Doris 产品介绍](https://doris.apache.org/docs/4.x/getting-started/what-is-apache-doris/)
- [系统架构：FE、BE 与两种部署方式](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/)
- [SELECT 查询](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-query/SELECT/)
- [Duplicate Key 明细模型](https://doris.apache.org/docs/4.x/table-design/data-model/duplicate/)
- [Doris 数据类型](https://doris.apache.org/docs/4.x/table-design/data-type/)
- [All-in-One 教学镜像](https://doris.apache.org/community/developer-guide/all-in-one-image/)
