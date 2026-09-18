# 单元 D04：湖表与内部表关联

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 30 分钟，包含动手实验和测验 |

[课程目录](../README.md) · [打开实验 4](lab4_query_iceberg.ipynb) · [打开测验 4](quiz4_internal_files_and_lake_tables.ipynb)

## 单元目标

本单元介绍内部表、外部文件和湖表的区别，以及通过 Doris 访问已有湖表的方式。

在准备好 Iceberg 环境后，你将能够查询湖上订单、关联内部表，并核对导入前后的结果。

## 学习目标

完成本单元后，你应该能够：

1. 区分 Doris 内部表、Parquet 文件、Iceberg 表与 External Catalog。
2. 根据探索和重复分析的需求选择外部直查或导入内部表。
3. 解释 Catalog、Database、Table 如何定位一张外部表。
4. 通过关联前后对照发现维表重复或关联缺失。
5. 在具备 Iceberg 环境时核对直查、关联和导入后的同一批订单。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| D04-01：内部表、外部文件与湖表查询 | 对象对照与 SQL | 5 分钟 | 区分访问路径，解释 JOIN 和导入边界 |
| 实验 4 | 动手操作（需 Iceberg） | 20 分钟 | 核对直查、关联、导入均为十单、12220.60 |
| 测验 4 | 交互测验 | 5 分钟 | 检查对象、访问选择、表定位与关联结果 |

## D04-01：内部表、外部文件与湖表查询

### 先分清文件、表和访问入口

假设历史订单已经存放在数据湖，近期客户资料在 Doris 中。
分析师希望一起查询它们，不希望为了探索一个问题先迁移所有历史。
先分清下面四种对象：

| 对象 | 管理什么 | 与其他对象的关系 |
| --- | --- | --- |
| Doris 内部表 | Doris 管理的表结构和数据 | 可以作为导入后的分析表 |
| Parquet 文件 | 按列编码的数据文件 | 可以单独读取，也可以是湖表的数据文件 |
| Iceberg 表 | 表元数据、快照以及所引用的数据文件 | 不是一个放有 Parquet 的目录 |
| External Catalog | Doris 访问外部系统元数据和表的入口 | 不是把全部外部数据复制到内部表 |

把一批 Parquet 放进对象存储，不会自动生成 Iceberg 的表元数据。
同样，创建 Catalog 也不等于完成数据导入。

```text
独立 Parquet ── 文件 TVF ──────────┐
                                 ├─ SQL 查询结果
Iceberg 元数据与数据文件 ─ Catalog ┘      │
                                       └─ INSERT INTO ... SELECT
                                                  │
                                                  ▼
                                             Doris 内部表
```

### 什么时候直查，什么时候导入？

| 场景 | 可以先选择 | 还要考虑什么 |
| --- | --- | --- |
| 第一次探索历史数据 | 通过 Catalog 直查 | 外部服务、网络、权限与元数据是否可用 |
| 将湖上历史与内部客户关联 | 跨 Catalog JOIN | 关联键是否唯一，是否有缺失客户 |
| 反复查询同一份业务快照 | 导入内部表后分析 | 同步频率、存储成本和变更更新方式 |
| 只检查一个独立文件 | 文件 TVF | 文件格式、字段和访问参数 |

“先直查”是一种接入选择，不保证所有外部查询都同样快；
“导入”保存的是此次查询得到的结果，不会自动建立长期同步任务。
文件 TVF 的对象存储接入在 D05 介绍，本 Lab 不运行独立文件实验。

### Catalog 如何定位外部表？

完整表名是 `catalog.database.table`。例如讲师可以把湖表注册为
`wwi_lake.sales.orders`：wwi_lake 是 Doris 中的 Catalog 名，sales 是外部数据库，
orders 是湖表。它不是 Parquet 的文件路径。

以下是查询形状示例；必须把外部表名替换成讲师提供的实际名称。
课程 Notebook 从 `DW_ICEBERG_ORDERS` 读取该名称，不要求照用示例名。

```sql
SELECT order_date, COUNT(*) AS sample_orders, SUM(order_amount) AS amount
FROM wwi_lake.sales.orders
GROUP BY order_date
ORDER BY order_date;
```

若外部表装入的是本课程十单投影，预期为第一天五单、3944.20，
第二天五单、8276.40。这里是查询外部表，尚未写入 Doris 内部表。

### JOIN 之后为什么还要数行？

假设一个客户有两行维表记录，一笔 100.00 的订单就可能关联成两行，
汇总后变成 200.00。JOIN 语法正确，不意味着业务金额正确。
反过来，内连接时缺少客户也会让订单消失。

```text
订单：order_id=1，customer_id=832，amount=2300.00
          │ 按 customer_id 关联
          ├─ 客户行 A → 一条订单结果
          └─ 客户行 B → 又一条订单结果（重复）
```

图中重复客户是说明关联放大的假设，不是说原 WWI 客户表有这个错误。
Lab 用唯一键客户表，并检查关联前后行数、金额以及导入后的逐字段结果。

完成外部实验后，以下查询可在课程内部实验库再次检查关联缺失：

```sql
SELECT COUNT(*) AS missing_customers
FROM d04_orders o
LEFT JOIN d04_customers c ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL;
```

预期为 0；若不是，先核对客户键和数据范围，不要把缺失订单的汇总当作完整答案。

### 导入后检查什么？

Lab 显式选择六个字段写入 `d04_orders`，不依赖外部字段的隐含顺序。
查询结果落入内部表后，再执行：

```sql
SELECT order_date, COUNT(*) AS sample_orders, SUM(order_amount) AS amount
FROM d04_orders
GROUP BY order_date
ORDER BY order_date;
```

此结果应与直查湖表一致；再比对订单号、客户、日期、金额、明细数和来源，
避免不同错误在汇总中抵消。

**实验条件：** 当前 Lab 是尚未实测的外部集成实验，需要讲师预置 Iceberg
服务、Catalog 和十单样本。没有环境时先读讲义并完成测验，实验记录为未执行；
不能用另一张内部表替代湖表。示例不宣称已验证外部写入、Schema 演进或性能。

## 动手实验 4：湖表与内部表关联

开始前请完成 D01–D03；运行实验还需讲师预置可查询的 Iceberg 订单表，并使用课程独立实验库。

打开[实验 4](lab4_query_iceberg.ipynb)，按顺序完成：

1. 确认 DW_ICEBERG_ORDERS 指向讲师提供的真实外部表。
2. 直接查询湖上十笔订单，核对金额 12220.60。
3. 关联内部客户表并检查行数，再导入内部订单表进行对账。

完成后保存查询结果与差异原因；未执行的步骤不要标记为完成。

### 数据来源与说明

实验使用 Microsoft WWI 官方模拟批发业务的历史子集，保留原始客户与商品标识。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。
湖表应由讲师使用 datasets/wwi/sample.json 中 orders 的六个字段和十行数据预置，不能用内部表替代外部环境。

## 单元总结

- Parquet 是文件格式，Iceberg 是管理快照和文件的表格式；External Catalog 是访问入口，不是数据复制。
- 探索数据可以先直查，重复分析可以评估导入；导入后的刷新与变更处理仍需明确设计。
- catalog.database.table 定位外部表，文件路径不能代替完整表名；Notebook 使用讲师配置的表名。
- 重复维表键会放大 JOIN 结果，缺失键会丢失内连接结果；同时核对行数、金额和明细。
- 直查、关联和内部表是三个检查点；本样本都应对应十单、12220.60，外部环境未运行不能算实验完成。

## 知识测验 4：湖表与内部表关联

完成讲义和实验后，打开[测验 4](quiz4_internal_files_and_lake_tables.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。
尚未准备湖表环境时，可以先完成概念测验，但不要将实验标记为已完成。

## 官方参考资料

- [数据目录概览](https://doris.apache.org/docs/4.x/lakehouse/catalog-overview/)
- [Iceberg Catalog](https://doris.apache.org/docs/4.x/lakehouse/catalogs/iceberg-catalog/)
- [S3 文件表值函数](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/s3/)
- [INSERT INTO SELECT](https://doris.apache.org/docs/4.x/data-operate/import/import-way/insert-into-manual/)

官方文档会随版本更新；本课程实验版本及已验证环境见课程信息和[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)。
