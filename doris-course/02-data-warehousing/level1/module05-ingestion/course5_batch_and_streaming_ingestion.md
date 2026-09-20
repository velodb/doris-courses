# Module 5：批量与持续数据接入

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 110 分钟，包含讲义阅读、动手实验和测验 |

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
| 5.1 先决定访问还是导入 | 接入选择表 | 5 分钟 | 按来源与完成方式选择接入路径 |
| 5.2 数据类型与 Schema | 字段对照 | 5 分钟 | 区分可解析的数据与合格业务记录 |
| 5.3 默认值与列映射 | 映射与默认值示例 | 8 分钟 | 确定字段顺序与省略字段的含义 |
| 5.4 Stream Load、结果检查与重试 | 请求与响应 | 5 分钟 | 判断成功、拒绝和不确定状态 |
| 5.5 对象存储批量与 INSERT SELECT | SQL 与流程对照 | 8 分钟 | 区分查询、持久化和异步导入 |
| 5.6 Kafka 与 Routine Load | SQL 与任务流程 | 10 分钟 | 解释消费进度与业务状态的区别 |
| 5.7 Flink CDC 与 Doris Connector | 变更流程图 | 5 分钟 | 解释快照、增量和恢复 |
| 5.8 Streaming Job 与 CDC_STREAM | 同步 SQL 与模式选择 | 8 分钟 | 解释 Streaming Job、CDC_STREAM 与目标表如何配合 |
| 5.9 对象存储增量文件 | 任务 SQL 与文件进度 | 6 分钟 | 识别重复文件与迟到数据问题 |
| 实验 5 | 动手操作 | 45 分钟 | 导入 WWI 十表，检查模拟 CSV 重试与拒绝 |
| 测验 5 | 交互测验 | 5 分钟 | 检查路径选择、映射、结果、重试和金额口径 |

## 5.1 先决定访问还是导入

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
Module 4 的直查也可以不落表；落表后是否持续刷新，要另作决定。

### 历史业务与新订单

```text
WWI 历史 Parquet ─ Stream Load → wwi_*（10 张业务表）
模拟新订单 CSV  ─ Stream Load → orders_imported（10 笔教学订单）
                                      │
                                      └─ 后续 Module 6 准入、Module 7 状态变化
```

历史包保留订单、明细、客户、商品、发票、账款及字典，共 701,846 行。
“总行数”是十张表的行数之和，不是订单数。
模拟订单号 900001–900010，引用 WWI 客户和商品，但价格、地区和事件时间由课程定义。

**学习安排：** 先学习文件导入、字段映射与重试，再学习持续任务及其进度。
本节讲解文件、消息和数据库变更三类接入路径；
Lab 先用一个 CSV 练习 Stream Load、导入核对与重试，再扩展到十张历史 Parquet 表。
Kafka、Flink 和对象存储相关小节用于理解持续接入的选择与工作过程。

## 5.2 数据类型与 Schema

Schema 是表的结构约定，包括列名、类型和是否允许为空。
导入前要把文件字段与这份约定对齐：订单号用于标识订单，金额用于计算，
日期用于按天汇总。一个字段选什么类型，取决于后续怎样使用它。

例如税前金额需要保留两位小数，可以使用 `DECIMAL(18, 2)`：18 表示总位数，
2 表示小数位数。金额范围和精度都应满足业务要求。订单号可使用整数，
只需要统计日期的字段可使用 DATE，需要记录发生时刻的字段则使用 DATETIME。
允许为空的字段还需要约定 NULL 的含义，例如“支付时间未知”，避免与零值混用。
类型范围与精度规则见[数据类型](https://doris.apache.org/docs/4.x/table-design/data-type/)。

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
Module 6 会保留原始文本，再将不合格记录单独分流。

### 导入多表时，先核对订单与明细

Orders 一行是一笔订单，OrderLines 一行是商品明细。
JOIN 后如果直接 COUNT(*)，数到的是明细，不是订单。
本单元主线只检查订单、明细、客户和商品的行数、关系与金额。
十张表仍全部导入，供后续使用；不要求在这里掌握完整账务模型。
WWI 客户账款属于账户层，不能直接分摊为逐单支付；
详细 SQL 放在[扩展阅读：发票与账户收款](optional_invoice_and_receipts.md)，不作为本节必做实验。

完成历史表导入后，可以运行 Lab 中的日期分析：

```sql
SELECT o.OrderDate,
       COUNT(DISTINCT o.OrderID) AS orders,
       SUM(l.Quantity * l.UnitPrice) AS order_amount
FROM wwi_orders o
JOIN wwi_order_lines l ON o.OrderID = l.OrderID
GROUP BY o.OrderDate
ORDER BY o.OrderDate
LIMIT 10;
```

COUNT(DISTINCT) 按订单计数，SUM 按明细计算税前金额。
这张历史日报与 Module 1 的十单子集不是同一个统计范围。

## 5.3 默认值与列映射

一份 CSV 可能把客户号放在订单号前面，而 Doris 表按另一种顺序定义字段。
列映射就是明确告诉导入过程“第几个值是什么、应该写到哪一列”。
数值都能转换成功时，列顺序错误也可能悄悄造成订单号和客户号互换，
所以导入后还要抽查具体订单的字段。

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

### 为省略字段约定默认值

默认值规定写入省略某列时填入什么。例如，专门接收新建订单的入口可以约定
初始状态为 CREATED；接收多种订单状态的入口则应保留源数据中的状态。
数据来源字段也可以使用该接入任务约定的来源标识。
支付是否成功、实际支付金额等业务事实，应由源系统提供。

**SQL 阅读示例：不随 Lab 执行。** 如需动手，使用独立实验库中尚不存在的
orders_defaults_reading 表，只运行一次；不要把示例写入 orders_imported。

<!-- reading-only-example -->
```sql
CREATE TABLE orders_defaults_reading (
    order_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT "CREATED"
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1");
INSERT INTO orders_defaults_reading (order_id) VALUES (901001);
INSERT INTO orders_defaults_reading (order_id, status) VALUES (901002, 'PAID');
SELECT order_id, status FROM orders_defaults_reading ORDER BY order_id;
```

第一行省略 status，得到 `(901001, CREATED)`；第二行明确提供状态，得到 `(901002, PAID)`。
默认值处理的是“没有提供该列”，不是把任意错误值修正成 CREATED。
此表只说明默认值，不代表已经验证付款。

列映射还可以通过表达式完成转换，例如把源字段整理成目标列需要的格式；
生成列把计算表达式放在表定义里，由表负责计算，适用表达式受目标版本限制。
选择时看规则属于哪个层次：某个来源专用的格式转换放在导入映射中，
需要随表统一维护的派生字段再考虑生成列。例如源文件的金额以分记录，
导入映射可使用 `order_amount=amount_cents/100.0`；18000 分应得到 180.00 元，
需同时设置目标小数类型。与此不同，生成列是表定义中的表达式，不依赖某个 CSV 的列顺序。
本 Lab 练习显式字段映射，生成列不作为本 Lab 操作。
具体配置和限制参见 [Stream Load 文档](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)。

## 5.4 Stream Load、结果检查与重试

Stream Load 是通过 HTTP 请求把文件内容发送给 Doris 的导入方式。
你准备目标表和文件，发送请求，再根据返回的导入状态检查结果。
它适合本节的本地文件：数据由客户端推送，Doris 接收后解析字段、检查数据并提交导入事务。

### 看清请求的组成

本 Lab 将文件直接发送到课程沙箱的 BE HTTP 地址，由 BE 接收数据并参与导入事务。
下面的 `DW_BE_HTTP_URL` 表示该地址，`DW_DATABASE` 表示目标数据库。

下面用占位符说明 Lab 请求的结构：

```text
PUT <DW_BE_HTTP_URL>/api/<DW_DATABASE>/orders_imported/_stream_load
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

Lab 的第一次导入会展开完整的 Python HTTP 请求：URL 选择目标表，headers 对应上面的参数，
`requests.put(..., data=payload)` 发送文件字节，`response.json()` 取得导入结果。
之后再使用 `lab.stream_load()` 封装重复操作。独立练习沿用同一请求结构，
只替换文件、目标表、批次 label 和列映射，不需要从零猜测 HTTP 写法。

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
FROM orders_imported;
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

如果应用每次只写几行，频繁提交会增加事务和存储版本的开销。
Group Commit 可以把兼容的小写入合成较大的批次，减少每批固定开销。
选择模式时，重点看应用何时收到确认：`sync_mode` 等待合批导入完成后返回，
`async_mode` 在数据写入 WAL（预写日志）后返回，查询可见性还要等待后续提交。
本 Lab 使用 `off_mode`，便于逐批观察事务结果和 label 重试行为。
启用合批前应按所用接口检查参数与 label 支持条件，见[Group Commit 文档](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/group-commit-manual/)。

## 5.5 对象存储批量与 INSERT SELECT

如果历史订单已经放在 S3 或兼容对象存储中，可以让 Doris 直接读取这些文件。
这时需要准备文件路径、格式和读取权限，并决定先查询检查，还是提交批量导入任务。

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

以一个月的订单文件为例，可以先通过 TVF 查询字段、日期范围和总金额，
再用 INSERT INTO SELECT 选择目标列写入内部表。这条 SQL 同时表达了读取、转换和落表过程。
如果选择 Broker Load，则提交包含路径、格式和目标表的任务，之后检查任务是否到达 FINISHED，
再核对目标数据。前者便于用 SQL 描述加工，后者以异步导入任务组织批量装载。

重复执行前，要明确这批数据是追加、按键更新，还是重建指定范围。
例如向明细表再次追加同一个月的文件，会重复计入销售额；
重试策略需要同时考虑导入任务和目标表模型。
操作入口见[Broker Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/broker-load-manual/)。

### 阅读示例：先检查文件，再落表

**外部环境示例，不随 Lab 执行。** 本节及后面的 Kafka、CDC 示例使用独立演示表，
不向主线的 orders_imported 写入。实际运行前，先切换到独立实验库，准备外部服务，
替换尖括号占位符；地址必须能被 Doris 节点访问，不能照搬 Notebook 所在机器的 localhost。
凭据由实验环境提供，不把真实密钥保存到讲义或 Notebook。

假设自行准备的 Parquet 文件只有 order_id、order_amount 两列，内容为
`(901001, 180.00)`、`(901002, 80.00)`。它不是仓库中的完整 WWI 文件。
下面先创建空的明细表，再从文件写入：

<!-- external-service-example -->
```sql
CREATE TABLE orders_s3_demo (
    order_id BIGINT, order_amount DECIMAL(18,2)
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1");

INSERT INTO orders_s3_demo (order_id, order_amount)
SELECT order_id, order_amount FROM S3(
    "uri"="s3://<bucket>/batch/orders.parquet",
    "s3.endpoint"="<endpoint>", "s3.region"="<region>",
    "s3.access_key"="<access_key>", "s3.secret_key"="<secret_key>",
    "format"="parquet"
);
SELECT COUNT(*) AS orders, SUM(order_amount) AS amount FROM orders_s3_demo;
```

阅读和操作时分三步：

1. 先单独取出 `SELECT ... FROM S3(...)` 执行，预期看到两条文件记录，内部表仍为空。
2. 再执行完整的 INSERT INTO SELECT，把这两列写入内部表；uri 选文件，format 指定解析格式。
3. 最后一条查询预期得到 2、260.00。不要为“确认成功”再次运行 INSERT，否则明细表会追加同一批记录。

S3 TVF 的参数说明见
[S3 TVF](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/s3/)。

## 5.6 Kafka 与 Routine Load

当上游不断产生订单消息时，无法等“整个文件准备好”再导入。
Kafka 负责保存持续到达的消息，Routine Load 则是在 Doris 中创建的持续消费任务：
它从指定 Topic 读取消息，按批次写入目标表，并维护消费进度。

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
例如某分区中已有位置 100、101 的两条订单消息，任务完成这批写入并提交进度后，
会继续消费后面的消息。下次检查时，如果 Kafka 已产生很多新消息，而任务进度长期停留，
就需要检查暂停原因、错误记录或处理能力。
不同 Partition 各自有位置编号，不能用一个分区的 offset 与另一个分区比较业务先后。
任务负责持续消费；观察时既看任务状态和暂停原因，也看提交进度、错误行与目标数据。
暂停、恢复、停止任务是任务生命周期，不是启动或停止 Doris 集群。

offset 回答“读到了哪里”，业务 event_version 回答“同一订单哪个版本更新”。
例如先消费签收、后消费迟到的支付事件，消费进度向前不代表应把订单状态倒退。
Module 7 会用订单事件进一步说明版本裁决。任务参数与管理方式见
[Routine Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/routine-load-manual/)。

### 阅读示例：创建任务后，还要看消费结果

**外部环境示例，不随 Lab 执行。** 准备一个独立 Kafka Topic，仅发送两条无表头 CSV 消息：
`901001,180.00` 和 `901002,80.00`。下面将消息的第一、二列映射为订单号、金额：

<!-- external-service-example -->
```sql
CREATE TABLE orders_kafka_demo (
    order_id BIGINT, order_amount DECIMAL(18,2)
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1");

CREATE ROUTINE LOAD orders_kafka_job ON orders_kafka_demo
COLUMNS TERMINATED BY ",",
COLUMNS(order_id, order_amount)
PROPERTIES("strict_mode"="true", "max_filter_ratio"="0", "max_error_number"="0")
FROM KAFKA(
    "kafka_broker_list"="<broker>:9092",
    "kafka_topic"="<dedicated_topic>",
    "property.kafka_default_offsets"="OFFSET_BEGINNING"
);
SHOW ROUTINE LOAD FOR orders_kafka_job;
SELECT COUNT(*) AS orders, SUM(order_amount) AS amount FROM orders_kafka_demo;
```

`OFFSET_BEGINNING` 指定新任务从分区开头消费，不是每一批都回到开头。
等待这两条消息提交后，空表应变成 2 行、260.00；任务仍会等待新消息，不会因当前 Topic 读完而结束。
在 SHOW 结果中查看 State、Progress、Statistic；若暂停，检查 ReasonOfStateChanged 和 ErrorLogUrls。
不要重新创建任务来代替正常恢复，以免重新消费旧消息。操作字段见上面的 Routine Load 官方说明。
观察结束后，可用 `PAUSE ROUTINE LOAD FOR orders_kafka_job` 暂停，
继续观察时用 `RESUME ROUTINE LOAD FOR orders_kafka_job` 恢复。

## 5.7 Flink CDC 与 Doris Connector

### 从业务库日志走到数仓

CDC（Change Data Capture，变更数据捕获）读取业务库已经提交的新增、更新和删除。
例如订单从 CREATED 更新为 PAID，CDC 把这次变化传给下游，数仓就能更新对应订单。
Flink CDC 负责读取源端数据和变更，Flink 作业可以继续处理这些数据，
Doris Connector 则负责把处理结果写入 Doris。

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

接入已有订单库时，通常先读取初始快照，建立一份已有订单状态，再衔接后续变更日志。
任务运行中会记录检查点（Checkpoint），保存可用于恢复的读取位置与处理状态。
发生故障后，任务根据检查点恢复；源端日志也需要保留到恢复所需的位置。

源库中的一条 UPDATE，到了 Doris 端通常体现为同一主键的新状态；
DELETE 也需要由连接器按删除语义传递，目标表才能正确移除对应逻辑行。
因此，要一起确认源表主键、目标 Unique Key 和连接器的更新删除配置，
并用同一订单的新增、支付、取消等变化检查同步结果。

选择该路径时一起核对 Flink、CDC、Connector 和数据库版本，
再测试新增、更新、删除以及中断恢复。
配置入口见 [Flink Doris Connector](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)。

## 5.8 Streaming Job 与 CDC_STREAM

**版本与范围：** 本节介绍 Doris 4.1 起的持续同步路径；官方将 MySQL、PostgreSQL
相关能力标为 Experimental（实验性）。以下以 MySQL 单表为例，不随 Lab 执行，
运行前需按目标补丁版本准备源库、驱动与同步权限。
[MySQL SQL 映射同步](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-table/)

### 与 Flink 路径有什么不同？

两条路径都要处理上一节的“快照 → 增量 → 恢复”。区别在于谁组织同步任务：
Flink 路径由外部 Flink 作业运行；本节在 Doris 中创建 Streaming Job，
通过 CDC_STREAM 表值函数读取源库，再按 SQL 映射写入目标表。

```text
MySQL 已有订单与 Binlog → CDC_STREAM → SELECT 字段映射 → Doris Unique Key 表
                         └──── Streaming Job 持续组织执行并记录进度 ────┘
```

| 部分 | 负责什么 | 本例中要填写什么 |
| --- | --- | --- |
| CDC_STREAM | 读取源库数据与变更 | JDBC 地址、驱动、账号、源库和源表 |
| SELECT / INSERT INTO | 映射字段并选择目标 | order_id、status，以及已创建的目标表 |
| Streaming Job | 组织持续运行并保存进度 | 任务名、启动位置和运行配置 |

`CREATE JOB ... ON STREAMING` 创建持续任务，
`INSERT INTO ... SELECT ... FROM CDC_STREAM(...)` 描述读取结果怎么落表。
SQL 映射模式需要预先创建 Unique Key 目标表；源端删除如何传递、主键怎样映射也需验证。
[CDC_STREAM](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/cdc-stream/)

### 启动位置与同步范围怎么选？

- `offset="initial"`：先读取已有订单，再衔接增量变化。
- `offset="latest"`：只接收启动后的增量，不会补齐启动前的订单。
- 单表需要选择列、改名或转换类型时，使用本例的 SQL 映射模式。
- 一组表按源结构接入时，可以评估 `FROM MYSQL (...) TO DATABASE ...` 自动建表同步。
  初次建表与后续 Schema 变化是两件事，不能假定所有变更都会自动兼容。
  [自动建表同步](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-database/)

开始前确认 MySQL 行模式 Binlog、同步账号、JDBC 驱动、源表及目标主键；
源日志必须保留到故障恢复需要的位置。任务进度回答“同步到哪里”，
Module 7 的业务版本规则回答“同一订单哪个状态更新”，两者都要与目标数据核对。

### 阅读示例：把配置对应到一笔订单

**外部环境示例，不随 Lab 执行。** 按上述条件准备 MySQL 与驱动；源表 demo.orders
包含主键 order_id 和 status，初始只有 `(901001, 'CREATED')`。
下面在 Doris 中预先创建同粒度的目标表，再创建同步任务：

<!-- external-service-example -->
```sql
CREATE TABLE orders_cdc_demo (
    order_id BIGINT NOT NULL, status VARCHAR(20)
) UNIQUE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_unique_key_merge_on_write"="true");

CREATE JOB orders_mysql_job ON STREAMING DO
INSERT INTO orders_cdc_demo (order_id, status)
SELECT order_id, status FROM CDC_STREAM(
    "type"="mysql", "jdbc_url"="jdbc:mysql://<mysql_host>:3306",
    "driver_url"="<driver_jar_url>", "driver_class"="com.mysql.cj.jdbc.Driver",
    "user"="<sync_user>", "password"="<sync_password>",
    "database"="demo", "table"="orders", "offset"="initial"
);
SELECT * FROM jobs("type"="insert")
WHERE ExecuteType = 'STREAMING' AND Name = 'orders_mysql_job';
SELECT order_id, status FROM orders_cdc_demo ORDER BY order_id;
```

按“初始化 → 产生变化 → 核对结果”观察：先等目标表出现 CREATED，
再在 MySQL 中把同一订单改成 PAID，等待同步后查 Doris，预期仍为一行、状态变为 PAID。
`offset=initial` 决定从快照衔接增量，SELECT 两列决定映射，Unique Key 决定目标行的身份。
状态和进度在 jobs() 中观察，但必须用最后一条订单查询确认业务变化已经到达。
该阅读示例的版本前提与配置见
[MySQL SQL 映射同步](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-table/)。
观察结束后，可用 `PAUSE JOB WHERE jobName = 'orders_mysql_job'` 暂停，
继续时用 `RESUME JOB WHERE jobName = 'orders_mysql_job'` 恢复。
操作语法见[持续导入任务管理](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-overview/)。

## 5.9 对象存储增量文件

### 新文件发现也是一种进度问题

固定文件集导完即可结束，持续目录会不断新增文件。
官方 Streaming Job + S3 TVF 路径面向后一类需求；
一次普通 S3 查询本身不是持续任务。

| 情景 | 处理方式 |
| --- | --- |
| 09:00 出现 orders-001.parquet | 读取文件并记录文件处理进度 |
| 09:05 出现 orders-002.parquet | 文件名大于已处理进度，作为新文件读取 |
| 09:10 才出现 orders-000.parquet | 文件名小于已处理进度，需要安排单独补数 |

这条路径按文件名的字典序判断新文件：新文件名必须大于最后已加载的文件名。
上游可以采用固定宽度、递增的批次编号，保持发布顺序与文件名顺序一致。
迟到订单的业务日期可以是昨天，但承载它的新文件仍应使用向前递增的发布编号。

任务的 `CurrentOffset` 表示已经处理到哪个文件，`EndOffset` 表示本批结束位置。
排查“文件到了但表里没有”时，先比较文件名与这两个进度，再检查路径匹配、任务错误和目标订单。
同名覆盖文件不应当作新的发布批次。规则与参数见
[对象存储持续导入](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/)。

### 阅读示例：给文件查询加上持续任务

**外部环境示例，不随 Lab 执行。** 沿用 5.5 的两列文件结构，另建空表，
并使用只包含增量文件的独立目录；不要指向已批量导入过的历史目录。

<!-- external-service-example -->
```sql
CREATE TABLE orders_files_demo LIKE orders_s3_demo;
CREATE JOB orders_files_job ON STREAMING DO
INSERT INTO orders_files_demo (order_id, order_amount)
SELECT order_id, order_amount FROM S3(
    "uri"="s3://<bucket>/incremental/orders-*.parquet",
    "s3.endpoint"="<endpoint>", "s3.region"="<region>",
    "s3.access_key"="<access_key>", "s3.secret_key"="<secret_key>",
    "format"="parquet"
);
SELECT * FROM jobs("type"="insert")
WHERE ExecuteType = 'STREAMING' AND Name = 'orders_files_job';
SELECT order_id, order_amount FROM orders_files_demo ORDER BY order_id;
```

先发布仅含 `(901001, 180.00)` 的 orders-001.parquet，等 CurrentOffset 推进且表内出现该行；
再发布仅含 `(901002, 80.00)` 的 orders-002.parquet，预期最终两行、合计 260.00。
与 5.5 不同，CREATE JOB 让文件查询持续运行；无需手工重复 INSERT。
若此后发布 orders-000.parquet，按本节规则不会作为新文件被读取，应另行安排补数。
任务语法与进度字段见上面的对象存储持续导入说明。
观察结束后同样暂停任务，使用 `PAUSE JOB WHERE jobName = 'orders_files_job'`，避免继续消费后续文件。

## 动手实验 5：批量导入、失败与重试

开始前请完成 Module 1–4 的相关概念，并保持课程沙箱运行；工具会自动配置同一容器的 BE HTTP 接入地址，继续使用课程独立实验库。

打开[实验 5](lab5_stream_load.ipynb)，按顺序完成：

1. 导入模拟新订单 CSV，查看响应并核对十行、1400.00。
2. 在 label 有效期内重试同一批次，再观察错误批次的拒绝结果。
3. 将同样的接入方式扩展到十张 WWI 历史 Parquet 表，检查主键、关联和金额。
4. 独立完成新 CSV 的列映射，核对订单号、客户号和金额。

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
- [CDC_STREAM 表值函数](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/cdc-stream/)
- [MySQL 单表 SQL 映射同步](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-table/)
- [MySQL 自动建表同步](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-mysql-database/)
- [对象存储持续导入](https://doris.apache.org/docs/4.x/data-operate/import/import-way/streaming-job/continuous-load-s3/)
