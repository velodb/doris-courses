# D02：观察存储和写入批次

本讲义服务于 Data Warehousing with Apache Doris Level 1。版本：首版草稿；已实现与待补实验分别标明。

[课程入口](../README.md) · [动手实验](lab2_observe_storage.ipynb) · [知识测验](quiz2_storage_and_write_batches.yaml)

## 学习目标

读完后能解释本单元的业务问题，在 Lab 中核对证据，并说清尚未验证的边界。先阅读数据契约，再运行实验，不将未执行的部分当作完成。

## D02-01：列式存储与查询路径

列式存储按列组织数据，查询只需少数列时有机会减少读取。但行数、列选择、过滤条件与缓存都会影响扫描量，不能凭一次耗时判断原因。

FE 决定查询计划，BE 执行扫描和计算。先用 EXPLAIN 看计划，再在较大样本上用 Query Profile 看实际工作量。首版 Lab 只展示基础计划，不提供性能结论。

**演示安排：** 查看同一订单表的 CREATE TABLE、EXPLAIN 和业务结果。录制所需的大样本 Profile 对照尚待补充。

## D02-02：Tablet、Rowset、Segment 与 Compaction

表可以按分区组织；分区中的桶对应数据分片 Tablet。写入会产生数据版本及 Rowset，Rowset 内包含 Segment；后台 Compaction 合并数据，降低大量小片段带来的读取负担。

SHOW TABLETS 提供 Tablet 和副本信息，版本相关指标不是 Rowset/Segment 的直接清单。讲授时应区分元数据证据和内部机制说明。单副本实验没有证明故障恢复。

**演示安排：** 展示分区与 Tablet 元数据，解释字段；Rowset 管理接口的受控观察作为待补录制内容。

## D02-03：写入批次与可见性

将同样十行一次写入和逐行写入，可以观察事务边界的差异。为了控制变量，首版明确关闭 Group Commit，并保持表结构、桶数和逻辑数据相同。

后台合并可能在采样前完成。没有观察到持续版本积压不代表攒批无意义，也不能强行写出固定性能倍数。数据何时可见需要结合具体写入方式判断。

**演示安排：** 运行两种写入方式，对账均为十行、1400.00，再比较版本相关观测。

## 复习与实验检查

完成本单元五道测验；对照 Notebook 的预期结果，保存实际运行版本、业务结果与失败原因。测验不要求数据库连接，Lab 不能由测验成绩替代。

参考：[Apache Doris 文档](https://doris.apache.org/docs/4.x/gettingStarted/what-is-apache-doris/)；实现与验证范围以[课程状态](../../README.md)为准。
