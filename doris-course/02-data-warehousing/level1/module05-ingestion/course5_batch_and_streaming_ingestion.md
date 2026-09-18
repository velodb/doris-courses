# 单元 D05：批量导入、失败与重试

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 70 分钟，包含动手实验和测验 |

[课程目录](../README.md) · [打开实验 5](lab5_stream_load.ipynb) · [打开测验 5](quiz5_load_methods_and_retry_safety.ipynb)

## 单元目标

本单元介绍常见的数据接入方式，以及导入结果检查和重试的基本方法。

完成本单元后，你将能够使用 Stream Load 导入订单文件，核对结果，并验证失败和重试时的数据变化。

## 学习目标

完成本单元后，你应该能够：

1. 按本地文件、对象存储、消息流和数据库变更选择接入路径。
2. 说明 CSV 列映射、Parquet 字段和默认值各自的作用。
3. 结合导入响应、加载行数、关联和金额判断结果是否正确。
4. 区分导入批次重试、业务事件去重与持续任务恢复。
5. 区分 WWI 历史订单、账户收款与课程模拟新订单的粒度和口径。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| D05-01：先决定访问还是导入 | 接入选择表 | 5 分钟 | 按来源与完成方式选择接入路径 |
| D05-02：数据类型与 Schema | 字段对照 | 5 分钟 | 区分可解析的数据与合格业务记录 |
| D05-03：默认值与列映射 | 映射示例 | 5 分钟 | 确定字段顺序与省略字段的含义 |
| D05-04：Stream Load、结果检查与重试 | 请求与响应 | 5 分钟 | 判断成功、拒绝和不确定状态 |
| D05-05：对象存储批量与 INSERT SELECT | 流程对照 | 5 分钟 | 区分查询、持久化和异步导入 |
| D05-06：Kafka 与 Routine Load | 任务流程图 | 5 分钟 | 解释消费进度与业务状态的区别 |
| D05-07：Flink CDC 与 Doris Connector | 变更流程图 | 5 分钟 | 解释快照、增量和恢复 |
| D05-08：Streaming Job 与 CDC_STREAM | 模式对照 | 5 分钟 | 区分任务、数据源和目标表 |
| D05-09：对象存储增量文件 | 文件进度案例 | 5 分钟 | 识别重复文件与迟到数据问题 |
| 实验 5 | 动手操作 | 20 分钟 | 导入 WWI 十表，检查模拟 CSV 重试与拒绝 |
| 测验 5 | 交互测验 | 5 分钟 | 检查路径选择、映射、结果、重试和金额口径 |

## D05-01：先决定访问还是导入

### 从数据来源选择入口

建设数仓通常先接历史存量，再持续接收变化。文件后缀不能决定全部方案：
同样是 Parquet，本地文件、对象存储上的固定文件集和不断增加的目录，
需要不同的传输方式与进度管理。

| 数据在哪里、怎样产生 | 接入选择 | 主要检查 |
| --- | --- | --- |
| 本地 CSV、JSON 或 Parquet，一次有限批次 | Stream Load | 请求结果、加载行数和目标表 |
| 对象存储上的固定文件集，希望先用 SQL 检查 | S3 TVF；需要落表时配合 INSERT INTO SELECT | 查询与写入是否分别完成 |
| 对象存储上的大批量文件，使用异步导入任务 | Broker Load | 导入任务最终状态和目标数据 |
| Kafka 持续产生消息 | Routine Load | 任务状态、提交进度和目标数据 |
| 业务数据库不断增删改 | CDC 路径，例如 Flink CDC + Doris Connector | 初始快照、增量和故障恢复位置 |
| 许多很小的写入请求 | 在兼容写入方式上评估 Group Commit | 确认模式、响应与可见性 |

这些是按需求选择的路径，不是必须顺序经过的六道工序。
D04 的直查也可以不落表；落表后是否持续刷新，要另作决定。

### 本节连接两类数据，不混淆来源

```text
WWI 历史 Parquet ─ Stream Load → d05_wwi_*（10 张业务表）
模拟新订单 CSV  ─ Stream Load → d05_stream（10 笔教学订单）
                                      │
                                      └─ 后续 D09-A 准入、D06 状态变化
```

历史包保留订单、明细、客户、商品、发票、账款及字典，共 701,846 行。
“总行数”是十张表的行数之和，不是订单数。
模拟订单号 900001–900010，引用 WWI 客户和商品，但价格、地区和事件时间由课程定义。

**动手范围：** 本 Lab 执行本地 Parquet/CSV 的 Stream Load，不运行 S3、Kafka、
CDC 或 Group Commit。后续小节解释这些路径的选择与工作方式，
不是要求学员先部署所有服务。

## D05-02：数据类型与 Schema

### 可以转换，不一定符合业务规则

| 字段 | 技术要求 | 业务要求 |
| --- | --- | --- |
| order_id | 可表示为整数 | 必须存在，且粒度是一笔订单 |
| order_amount | 可表示为明确精度的小数 | 金额符号与业务口径合理 |
| customer_id | 可表示为整数 | 客户在客户维表中存在 |
| event_time | 可表示为日期时间 | 表示业务发生时间，不冒充接入时间 |
| data_source | 可表示为字符串 | 区分 WWI 与 COURSE_SIMULATION |

`not-a-number` 无法作为金额导入；客户号 `999999` 却可以转换成整数，
仍可能找不到对应客户。前者是类型问题，后者需要业务关联校验。
D09-A 会保留原始文本，再将不合格记录单独分流。

### 历史表的粒度也属于 Schema 理解的一部分

Orders 一行是一笔订单，OrderLines 一行是商品明细。
JOIN 后如果直接 COUNT(*)，数到的是明细，不是订单。
WWI 客户账款又属于账户层，不能因为出现收款就分摊到某笔订单。

完成历史表导入后，可以运行 Lab 中的日期分析：

```sql
SELECT o.OrderDate,
       COUNT(DISTINCT o.OrderID) AS orders,
       SUM(l.Quantity * l.UnitPrice) AS order_amount
FROM d05_wwi_orders o
JOIN d05_wwi_order_lines l ON o.OrderID = l.OrderID
GROUP BY o.OrderDate
ORDER BY o.OrderDate
LIMIT 10;
```

COUNT(DISTINCT) 按订单计数，SUM 按明细计算税前金额。
这张历史日报与 D01 的十单子集不是同一个统计范围。

## D05-03：默认值与列映射

### CSV 要明确顺序，Parquet 要对齐字段

模拟 CSV 没有表头。Lab 显式传入以下列顺序：

```text
order_id,customer_id,order_amount,status,event_version,event_id,
event_time,paid_amount,refund_amount,region,data_source
```

`columns` 描述输入值如何对应目标列；它不是让 Doris 猜测每个值的业务含义。
本课 Parquet 按 manifest 中的字段定义创建目标表，不使用 CSV 的逗号分隔设置。

| 输入 | 格式设置 | 列解释方式 |
| --- | --- | --- |
| 模拟 orders.csv | format=csv，column_separator=逗号 | 显式 columns，与文件顺序一致 |
| WWI orders.parquet 等 | format=parquet | 文件字段与课程 DDL 对齐 |

### 默认值不能代替未知的业务事实

默认值描述省略字段时的约定，不是修复错误数据的万能方式。
例如未知支付状态不能默认成 PAID，缺失金额也不能随意补成 0。
新增一个带默认值的技术字段，与补造真实支付事实，是两件事。

字段映射可以承载导入转换；生成列则由表定义中的表达式计算。
本 Lab 只演示显式映射，不执行默认值、生成列或复杂转换的独立实验。
具体配置和限制参见 [Stream Load 文档](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)。

## D05-04：Stream Load、结果检查与重试

### 看清请求的组成

本课程工具把文件推送到已明确配置的同集群 BE HTTP 地址，
由 BE 接收字节并参与导入事务。不把 FE 地址填入 `DW_BE_HTTP_URL`；
课程工具不自动跟随重定向。

下面是 Lab 请求的结构说明，不是包含真实凭据的可复制请求：

```text
PUT <DW_BE_HTTP_URL>/api/<DW_DATABASE>/d05_stream/_stream_load
label: <本批次唯一标识，重试时保留>
format: csv
column_separator: ,
columns: <上节列顺序>
strict_mode: true
max_filter_ratio: 0
group_commit: off_mode
请求体：orders.csv 原始字节
```

历史 Parquet 使用 format=parquet，不附 CSV 分隔符。
参数决定解析和质量处理方式；所有请求都要检查返回的 JSON，而不只看 HTTP 状态。

### 响应与表内结果要一起看

以下是成功导入模拟十单时需要核对的字段示意，并非完整响应：

```json
{
  "Status": "Success",
  "NumberLoadedRows": 10,
  "NumberFilteredRows": 0
}
```

再核对业务结果：

```sql
SELECT COUNT(*) AS orders, SUM(order_amount) AS amount
FROM d05_stream;
```

预期十笔、1400.00。历史数据还要检查主键重复、客户/商品关联和金额，
因为十表全部导入成功仍可能包含错误的关联口径。

| 响应情景 | 判断与下一步 |
| --- | --- |
| Success | 检查加载/过滤行数，再核对目标表 |
| Label Already Exists | 检查原批次状态；不是“本次又成功写入一批” |
| Fail | 保留错误信息，查明拒绝原因，不把失败批次算入业务数据 |
| Publish Timeout 或请求结果不确定 | 保留 label 与事务信息，确认原事务结果；不要换新 label 盲目追加 |

### 重试保护不等于永久去重

Lab 在 label 有效期内重发相同文件和相同 label，预期不再追加十行。
若换成新 label，对 Duplicate Key 表就是另一批追加；业务去重还要依赖稳定键和版本。

坏数据实验使用新 label、两行输入，其中一行金额无法转换。
在本课 strict_mode=true、max_filter_ratio=0 的设置下，预期整批拒绝，
原有十行和 1400.00 保持不变。ErrorURL 是排错线索，不是长期拒收表。

Group Commit 是对兼容小写入进行合并的机制，不是新的数据源连接器。
本 Lab 固定 off_mode；不能把这里的响应、可见性和 label 结论直接套到
sync_mode 或 async_mode。模式差异见 [Group Commit 文档](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)。

## D05-05：对象存储批量与 INSERT SELECT

### 查询文件与保存结果是两个动作

```text
对象存储固定文件集 → S3 TVF → SELECT 结果
                                  │
                                  └─ INSERT INTO SELECT → 内部表
对象存储固定文件集 → Broker Load 任务 ── 完成后核对 → 内部表
```

TVF（表值函数）把文件暴露成 SQL 可以读取的关系。
单独 SELECT 不会建立长期内部表；INSERT INTO SELECT 才把选定结果写入目标表。
Broker Load 是异步导入路径，提交被接受不等于任务已完成。

例如先查看 orders.parquet 的字段，再选择需要的列落表；
若再导一次相同文件，要先判断目标是全量重建、批次追加还是按键更新。
“文件路径没变”不自动等于“再次执行不会重复”。

本课程 WWI 包目前由讲师本地分发，尚无课程 S3 下载入口。
本节不提供虚构的桶地址或假定可用的凭据；对象存储仅作概念讲解。

## D05-06：Kafka 与 Routine Load

### 持续任务要保存进度

```text
业务生产者 → Kafka Topic / Partition
                         │ 持续消费
                         ▼
                  Routine Load Job
                         │ 分批写入并推进消费进度
                         ▼
                    Doris 目标表
```

Topic 是消息集合，Partition 将其分片，offset 标识分区内的位置。
任务负责持续消费；观察时既看任务状态和暂停原因，也看提交进度、错误行与目标数据。
暂停、恢复、停止任务是任务生命周期，不是启动或停止 Doris 集群。

offset 回答“读到了哪里”，业务 event_version 回答“同一订单哪个版本更新”。
例如先消费签收、后消费迟到的支付事件，消费进度向前不代表应把订单状态倒退。
本课没有运行 Kafka 环境；D06 的本地事件重放只解释后一个问题。

## D05-07：Flink CDC 与 Doris Connector

### 从业务库日志走到数仓

CDC 是捕获已提交数据变更的方式，不是简单地定期全表导出。
Flink CDC 读取源端数据和变更，Doris Connector 把处理结果写入 Doris。

```text
源业务库：初始快照 + 后续变更日志
                 │
                 ▼
            Flink CDC
       处理变更、维护恢复状态
                 │ Doris Connector
                 ▼
              Doris 表
```

只做初始快照会漏掉后续变化，只从最新日志开始又可能没有历史。
需要说明两者如何衔接，以及任务中断后从哪里恢复。
检查点/日志位置是恢复依据，不等于订单的业务版本号。

选择该路径时一起核对 Flink、CDC、Connector 和数据库版本，
再测试新增、更新、删除以及中断恢复。
本课目前只讲流程，不要求运行这套外部环境；
配置入口见 [Flink Doris Connector](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)。

## D05-08：Streaming Job 与 CDC_STREAM

### 任务与数据源不是同一个对象

当前 4.x 官方文档描述两种 Streaming Job 模式：

| 模式 | 数据源与目标 | 理解重点 |
| --- | --- | --- |
| TVF 模式 | 从 S3 TVF 或 CDC Stream TVF 读取，写入指定表 | Job 管持续执行，TVF 管数据访问 |
| 多表 CDC 模式 | 从上游数据库同步到目标数据库 | 多表范围、初始同步与建表规则需要明确 |

CDC_STREAM 是 CDC 数据访问入口；Streaming Job 是持续运行的任务。
不能用“创建一个任务”替代对源表、目标键、快照、增量位置和恢复方式的说明。

这是概念介绍。官方 4.x 页面会覆盖不同补丁版本，不能据此认定所有选项均已在
课程目标 4.1.3 实测；本课不提供该路径的可执行 Lab。
具体模式与版本条件见 [CREATE STREAMING JOB](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/job/CREATE-STREAMING-JOB/)。

## D05-09：对象存储增量文件

### 新文件发现也是一种进度问题

固定文件集导完即可结束，持续目录会不断新增文件。
官方 Streaming Job + S3 TVF 路径面向后一类需求；
一次普通 S3 查询本身不是持续任务。

| 情景 | 需要回答的问题 |
| --- | --- |
| 09:00 出现 orders-001.parquet | 如何发现并记录该文件的处理状态？ |
| 09:05 又看到相同对象 | 如何避免把同一批订单重复追加？ |
| 09:10 才收到昨天的文件 | 如何发现迟到数据并更新业务统计？ |
| 任务写入后中断 | 恢复后哪些文件需要重试，怎样核对结果？ |

文件处理进度与业务日期是两条线。不能只按“今天的文件名”决定处理范围，
也不能把对象目录当作天然有消费确认的消息队列。
本节不执行持续文件实验；实际发现规则、限制和参数以目标版本的
[对象存储持续导入文档](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/)为准。

## 动手实验 5：批量导入、失败与重试

开始前请完成 D01–D04 的相关概念；配置同一集群的 BE HTTP 接入地址，并使用课程独立实验库。

打开[实验 5](lab5_stream_load.ipynb)，按顺序完成：

1. 校验本地 Parquet，导入 10 张 WWI 历史表，共 701,846 行，检查主键、关联和金额。
2. 导入模拟新订单 CSV，保留响应并核对十行、1400.00。
3. 在 label 有效期内重试同一批次，确认没有追加重复订单。
4. 导入含非法金额的批次，观察整批拒绝并检查原有数据未变。

完成后保存查询结果与差异原因；未执行的步骤不要标记为完成。

### 数据来源与说明

历史部分采用 Microsoft WWI；新订单及变更标为 COURSE_SIMULATION，引用 WWI 客户和商品，但不回填历史。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- 从来源、批次或持续性、完成方式选择接入路径；Stream Load、S3 TVF、Routine Load 和 CDC 不是一条固定流水线。
- CSV 需要明确列顺序和分隔符，Parquet 需要字段与 DDL 对齐；默认值不能补造支付等业务事实。
- 先看导入事务和加载/过滤行数，再查目标表的明细、关联和金额；HTTP 成功不够。
- 相同 label 识别有限期内的同批请求，业务键识别事件，任务进度用于恢复；三者不能互相替代。
- 历史订单明细、账户收款和模拟订单属于不同粒度和来源；保留 WWI 历史，不把账户收款强行分摊到订单。

## 知识测验 5：批量导入、失败与重试

完成讲义和实验后，打开[测验 5](quiz5_load_methods_and_retry_safety.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。
能解释结果和选择原因，比只记住命令名称更重要。

## 官方参考资料

- [数据导入概览](https://doris.apache.org/docs/4.x/data-operate/import/load-manual/)
- [Stream Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)
- [S3 文件表值函数](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/s3/)
- [INSERT INTO SELECT](https://doris.apache.org/docs/4.x/data-operate/import/import-way/insert-into-manual/)
- [Broker Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/broker-load-manual/)
- [Routine Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/routine-load-manual/)
- [Group Commit](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)
- [Flink Doris Connector](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)
- [CREATE STREAMING JOB](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/job/CREATE-STREAMING-JOB/)
- [对象存储持续导入](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/)

官方文档会随版本更新；本课程实验版本及已验证环境见课程信息和[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)。
