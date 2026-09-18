# Level 1：数据接入、清洗与更新

这一阶段从十笔 WWI 历史订单开始，逐步回答三个问题：
数据如何进入 Doris？错误记录如何识别？订单变化后如何保持分析结果正确？

**第一次学习从 [D01 讲义](module01-introduction/course1_introduction_to_apache_doris.md) 开始。**
不需要先运行所有 Notebook，也不需要先学习全部架构概念。

## 学习顺序

每个单元先读讲义，再执行 Lab，最后完成五道交互测验。
点击下表中的“Lab”或“Quiz”即可打开 Notebook。

本课程先提供中文版本，讲义按以下顺序组织：
课程信息 → 单元目标 → 学习目标 → 单元安排 → 分节讲解 →
动手实验 → 单元总结 → 知识测验 → 官方参考资料。
SQL、API 和产品名称保留原文；课程主题与学习顺序沿用数仓总纲。

| 顺序 | 单元与业务问题 | 动手实验 | 知识测验 |
| --- | --- | --- | --- |
| 1 · D01 | [连接 Doris，查询第一批订单](module01-introduction/course1_introduction_to_apache_doris.md) | [Lab 1](module01-introduction/lab1_connect_and_query.ipynb) | [Quiz 1](module01-introduction/quiz1_doris_fundamentals.ipynb) |
| 2 · D02 | [数据怎样存储，写入批次有什么影响？](module02-architecture/course2_doris_architecture.md) | [Lab 2](module02-architecture/lab2_observe_storage.ipynb) | [Quiz 2](module02-architecture/quiz2_storage_and_write_batches.ipynb) |
| 3 · D03 | [重复订单、分区与分桶怎样处理？](module03-table-design/course3_models_partitioning_and_bucketing.md) | [Lab 3](module03-table-design/lab3_models_and_pruning.ipynb) | [Quiz 3](module03-table-design/quiz3_models_and_data_distribution.ipynb) |
| 4 · D04 | [已有湖表怎样与内部表关联？](module04-external-access/course4_querying_external_data.md) | [Lab 4](module04-external-access/lab4_query_iceberg.ipynb) | [Quiz 4](module04-external-access/quiz4_internal_files_and_lake_tables.ipynb) |
| 5 · D05 | [怎样导入文件、判断失败并安全重试？](module05-ingestion/course5_batch_and_streaming_ingestion.md) | [Lab 5](module05-ingestion/lab5_stream_load.ipynb) | [Quiz 5](module05-ingestion/quiz5_load_methods_and_retry_safety.ipynb) |
| 6 · D09-A | [怎样保留输入并分离错误记录？](module09a-data-quality/course9a_data_quality_and_schema_validation.md) | [Lab 9A](module09a-data-quality/lab9a_validate_orders.ipynb) | [Quiz 9A](module09a-data-quality/quiz9a_data_quality_and_rejection.ipynb) |
| 7 · D06 | [怎样应对乱序、退款与事件重放？](module06-state-changes/course6_updates_deletes_and_replay.md) | [Lab 6](module06-state-changes/lab6_current_state_and_replay.ipynb) | [Quiz 6](module06-state-changes/quiz6_state_changes_and_replay.ipynb) |

编号对应课程总纲，不是运行顺序。D09-A 先产生合格订单，D06 再读取它。
按上表顺序学习，不要按文件夹名称排序运行。

## 数据怎样贯穿 Level 1？

| 单元 | 数据与业务结果 |
| --- | --- |
| D01–D03 | WWI 十单投影，税前金额 12220.60；查询、存储、模型与分区 |
| D04 | 同一 WWI 子集预置为真实 Iceberg 表，关联内部客户；需额外环境 |
| D05 | 10 张 WWI 历史表、701,846 行；再导入 10 笔模拟新订单 |
| D09-A | 13 行模拟输入 → 10 行合格、3 行拒收，验证 WWI 客户引用 |
| D06 | 11 笔当前订单、18 条逻辑历史、19 次投递；核对商品、支付、退款及配送 |

历史来源 WWI 与新增来源 COURSE_SIMULATION 分开，不能将客户账户收款强行分摊到历史订单。

## 实验表如何命名？

表名说明数据的业务含义或实验用途；单元编号仅用于课程导航。
例如，sample 表示小样本，current 表示当前状态，raw 表示保留原始输入。

| 表名 | 含义 |
| --- | --- |
| orders_sample | 入门查询使用的十笔历史订单样本 |
| orders_batch、orders_rowwise | 相同订单分别批量、逐行写入的对照表 |
| orders_duplicate、orders_unique、orders_aggregate | 对比三种表模型的实验表 |
| orders_partitioned | 观察日期分区和分桶裁剪的订单表 |
| orders_from_lake、customers_sample | 湖表导入结果与关联用的客户样本 |
| wwi_orders、wwi_customers 等 wwi_ 表 | 保留 WWI 来源的十张完整历史业务表 |
| orders_imported | Stream Load 导入的模拟新订单 |
| orders_raw、orders_classified | 原始订单输入与带校验结果的分类视图 |
| orders_clean、orders_rejected | 合格订单与拒收记录 |
| orders_current、order_events、event_deliveries | 当前订单、业务事件历史与消息投递记录 |
| order_items、payments、refunds、shipment_events | 商品明细、支付、退款与配送事件 |

customers 和 products 分别是从 WWI 历史表提取的客户、商品维度。
部分更新和删除练习使用 orders_partial_update、orders_delete_demo，
不修改订单当前表。每个 Lab 只重建自己拥有的表，跨单元依赖的表仅供读取。

## 开始前准备什么？

- 能阅读 SELECT、WHERE、GROUP BY 等基础 SQL。
- 按[环境准备](../environments/single-node/README.md)安装 Python 依赖。
- 准备 Docker Desktop / Engine 和 Compose 插件；课程使用单容器 Doris 沙箱。
- D01 负责启动沙箱，后续 Lab 自动连接同一环境。执行前先阅读各 Lab 的实验表重置范围。
- D01–D03 使用仓库内的 WWI 十单子集；D05 另需讲师提供本地 Parquet 包，按[数据说明](../datasets/README.md)准备。

## 怎样完成一个 Lab？

1. 阅读每步的目的与预期结果，再运行紧接着的代码。
2. 比较实际结果，说明金额、状态与行数分别代表什么。
3. 遇到差异，先查看明细和执行顺序，不要直接修改预期值。
4. 完成动手练习和测验，确认自己能解释结果，而不只是运行无报错。

重启内核后，重新执行该 Notebook 的初始化与连接单元。
课程实验只重建各自拥有的表，具体范围在每个 Lab 开头说明。

## 哪些部分需要额外准备？

七个单元均提供讲义、示例与测验；讲义中的概念介绍不等于所有外部实验均可直接运行。
D04 需要预置 Iceberg 湖表，没有环境时可以先阅读讲义并继续 D05，
之后补做湖表实验，不把跳过视为完成。

D05 当前可执行的是 Stream Load；Kafka、CDC、对象存储和 Group Commit
尚未作为完整实验交付。D02、D03 使用小数据理解存储和模型，不做性能排名。
环境版本与测试范围见[验证记录](../../../maintenance/02-data-warehousing/VALIDATION.md)。
