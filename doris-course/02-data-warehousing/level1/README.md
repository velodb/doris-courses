# Level 1：数据接入、清洗与更新

目标：从订单输入到可解释的质量结果，再到正确的当前状态和历史。
先读[环境准备](../environments/single-node/README.md)与[数据契约](../datasets/README.md)。

| 学习顺序 | 讲义 | 动手实验 | 测验 | 首版状态 |
|---|---|---|---|---|
| D01 | [连接 Doris，查询第一批订单](module01-introduction/course1_introduction_to_apache_doris.md) | [Lab](module01-introduction/lab1_connect_and_query.ipynb) | [Quiz](module01-introduction/quiz1_doris_fundamentals.ipynb) | 核心操作已在开发实例执行 |
| D02 | [观察存储和写入批次](module02-architecture/course2_doris_architecture.md) | [Lab](module02-architecture/lab2_observe_storage.ipynb) | [Quiz](module02-architecture/quiz2_storage_and_write_batches.ipynb) | 核心操作已在开发实例执行 |
| D03 | [模型语义与分区分桶](module03-table-design/course3_models_partitioning_and_bucketing.md) | [Lab](module03-table-design/lab3_models_and_pruning.ipynb) | [Quiz](module03-table-design/quiz3_models_and_data_distribution.ipynb) | 核心操作已在开发实例执行 |
| D04 | [湖表与内部表关联](module04-external-access/course4_querying_external_data.md) | [Lab](module04-external-access/lab4_query_iceberg.ipynb) | [Quiz](module04-external-access/quiz4_internal_files_and_lake_tables.ipynb) | 需要预置 Iceberg，未实测 |
| D05 | [批量导入、失败与重试](module05-ingestion/course5_batch_and_streaming_ingestion.md) | [Lab](module05-ingestion/lab5_stream_load.ipynb) | [Quiz](module05-ingestion/quiz5_load_methods_and_retry_safety.ipynb) | 核心操作已在开发实例执行 |
| D09-A | [保留输入、分流拒收、自动验收](module09a-data-quality/course9a_data_quality_and_schema_validation.md) | [Lab](module09a-data-quality/lab9a_validate_orders.ipynb) | [Quiz](module09a-data-quality/quiz9a_data_quality_and_rejection.ipynb) | 核心操作已在开发实例执行 |
| D06 | [乱序裁决、历史保留和可恢复重放](module06-state-changes/course6_updates_deletes_and_replay.md) | [Lab](module06-state-changes/lab6_current_state_and_replay.ipynb) | [Quiz](module06-state-changes/quiz6_state_changes_and_replay.ipynb) | 核心操作已在开发实例执行 |

顺序是 D01 → D02 → D03 → D04 → D05 → D09-A → D06。Module 编号用于
对应总纲，不代表文件名排序。D09-A 先产出合格订单，D06 再读取它；
其他 Lab 使用独立表，可以重复运行。D04 暂缺环境时，可以继续核心路径，
但需记录湖表实验未完成，不能据此宣称已完成全部 Level 1。

每个单元的建议学习方式：

1. 读讲义，明确要解决的业务问题和数据含义。
2. 执行 Lab，观察 SQL、实际结果与独立预期值。
3. 完成五道 Quiz，阅读选错时的解释。
4. 保存环境版本与发现的问题；不要提交带本机输出的 Notebook。

D05 的首版只执行 Stream Load；Kafka、CDC、对象存储和 Group Commit
仍在[集成待办](../integration-backlog.md)。D02、D03 小数据用于功能与
元数据观察，不作为性能基准。课程标注时长沿用规划，试讲尚未完成。
