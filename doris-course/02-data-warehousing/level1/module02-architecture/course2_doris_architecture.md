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

1. 描述 FE 规划查询、BE 执行扫描的基本路径。
2. 解释 Tablet、Rowset、Segment 与后台 Compaction 的关系。
3. 比较批量和逐行写入的业务结果，并说明哪些观测不能直接证明性能。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| D02-01：列式存储与查询路径 | 讲解与示例 | 5 分钟 | 解释列裁剪与查询计划的作用 |
| D02-02：Tablet、Rowset、Segment 与 Compaction | 讲解与示例 | 5 分钟 | 画出存储层次并解释后台合并 |
| D02-03：写入批次与可见性 | 讲解与示例 | 5 分钟 | 比较相同数据的不同写入方式 |
| 实验 2 | 动手操作 | 20 分钟 | 完成下方实验并核对结果 |
| 测验 2 | 五道知识测验 | 5 分钟 | 检查概念与场景选择 |

当前实验只观察小样本结果与基础元数据；大样本 Query Profile 和受控 Rowset 观察尚未补齐。

## D02-01：列式存储与查询路径

列式存储按列组织数据，查询只需少数列时有机会减少读取。但行数、列选择、过滤条件与缓存都会影响扫描量，不能凭一次耗时判断原因。

FE 决定查询计划，BE 执行扫描和计算。先用 EXPLAIN 看计划，再在较大样本上用 Query Profile 看实际工作量。首版 Lab 只展示基础计划，不提供性能结论。

**观察与练习：** 查看同一订单表的 CREATE TABLE、EXPLAIN 和业务结果。录制所需的大样本 Profile 对照尚待补充。

## D02-02：Tablet、Rowset、Segment 与 Compaction

表可以按分区组织；分区中的桶对应数据分片 Tablet。写入会产生数据版本及 Rowset，Rowset 内包含 Segment；后台 Compaction 合并数据，降低大量小片段带来的读取负担。

SHOW TABLETS 提供 Tablet 和副本信息，版本相关指标不是 Rowset/Segment 的直接清单。讲授时应区分元数据证据和内部机制说明。单副本实验没有证明故障恢复。

**观察与练习：** 展示分区与 Tablet 元数据，解释字段；Rowset 管理接口的受控观察作为待补录制内容。

## D02-03：写入批次与可见性

将同样十行一次写入和逐行写入，可以观察事务边界的差异。为了控制变量，首版明确关闭 Group Commit，并保持表结构、桶数和逻辑数据相同。

后台合并可能在采样前完成。没有观察到持续版本积压不代表攒批无意义，也不能强行写出固定性能倍数。数据何时可见需要结合具体写入方式判断。

**观察与练习：** 运行两种写入方式，对账均为十行、1400.00，再比较版本相关观测。

## 动手实验 2：观察存储和写入批次

开始前请完成 D01，能够连接实验实例并核对订单总量，并使用课程独立实验库。

打开[实验 2](lab2_observe_storage.ipynb)，按顺序完成：

1. 创建相同结构的批量写入表和逐行写入表。
2. 用相同订单样本执行两种写入方式，观察 Tablet 元数据。
3. 核对两表均为十行、1400.00，记录采样时刻和后台合并的影响。

完成后保存查询结果与差异原因；未执行的步骤不要标记为完成。

### 数据来源与说明

实验使用仓库自带的合成订单及变更样本，不含真实客户数据。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- 列式读取、执行计划与实际扫描量是相关但不同的观察角度。
- 写入会产生数据片段；Compaction 在后台合并，观测结果随时间变化。
- 先确认业务结果相同，再讨论系统开销；十行样本不能证明生产性能。

## 知识测验 2：观察存储和写入批次

完成讲义和实验后，打开[测验 2](quiz2_storage_and_write_batches.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。
能解释结果和选择原因，比只记住命令名称更重要。

## 官方参考资料

- [系统架构：FE、BE 与两种部署方式](https://doris.apache.org/docs/4.x/features-architecture/system-architecture/)
- [Compaction 原理](https://doris.apache.org/docs/4.x/admin-manual/trouble-shooting/compaction-principles/)
- [分区与分桶基础](https://doris.apache.org/docs/4.x/table-design/data-partitioning/basic-concepts/)

官方文档会随版本更新；本课程实验版本及已验证环境见课程信息和[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)。
