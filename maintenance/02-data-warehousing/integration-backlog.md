# Level 1 实验交付清单

更新于 2026-09-20。课程讲义、七个主线 Lab 与三份扩展 Notebook 是不同交付物；
“有讲解”“手工验证片段”“已有可重跑 Notebook”分开记录，不能互相替代。
数据基线以课程 `datasets/README.md`、manifest 和独立预期文件为准。
运行条件与结果见 [VALIDATION.md](VALIDATION.md)。

## 已有可运行材料

| 单元 | 主线 / 扩展已覆盖 | 尚未覆盖的边界 |
|---|---|---|
| Module 1 | WWI 十单连接、查询与汇总，10 行、12220.60 | 不验证容灾或生产规模 |
| Module 2–3 | 主线模型与小批次；扩展 100,000 行、100 批 vs 1 批、分区对照、逐行一致性与 Profile；Rowset 状态入口已有手工验证 | 持续写入压力、多请求合批、多节点分布与受控 Compaction 观察；不能从一次耗时推断性能 |
| Module 4 | Iceberg Catalog、关联与落地；扩展独立 Parquet TVF 与同一 WWI 十单逐行对账 | 其他 Catalog、演进和生产规模 |
| Module 5.3 | 讲义省略字段示例；扩展 DEFAULT、生成列按分转元，结果逐行核对 | 更多类型、表达式与模型组合 |
| Module 5.4 | 主线 Stream Load 失败与 label 重试；扩展 off/sync/async 的响应、首次完整可见时间、GroupCommit 标志与最终结果 | 多请求共享事务、持续吞吐、WAL 故障；不能把 off_mode label 结论套到合批 |
| Module 5.5 | WWI 十表 Parquet Stream Load；扩展 S3 TVF + INSERT SELECT、Broker Load，等 FINISHED 并逐行核对 | 更大批次、导入中断与失败恢复 |
| Module 6 | 13 输入 → 10 合格、3 拒收；完整质量对账与错误注入；扩展独立表加列和 INT→BIGINT，检查任务与下游投影 | 更复杂 Schema Change、真实动态新鲜度、调度集成 |
| Module 7 | 主线部分更新、软删除、SQL DELETE、乱序和人工重放；扩展导入删除版本裁决、暂存事件内容冲突检测 | 真实位点恢复；多写者原子拒收；不能把暂存检查叫作生产冲突处理服务 |

扩展入口：[Level 1 扩展实验](../../doris-course/02-data-warehousing/level1/extensions/README.md)。
扩展在独立 ext_* 表执行，不修改主线数据，不增加视频数。需要 PyArrow 的扩展依赖单独安装。

## 仍需交付的持续集成实验

| 单元 | 尚缺什么 | 补齐后如何验收 |
|---|---|---|
| Module 5.6 | Kafka/Routine Load 环境和九次模拟事件投递 | 记录位点、暂停/恢复、完整性及业务版本；不将消费进度当最新状态 |
| Module 5.7 | MySQL/Flink CDC/Connector 兼容环境 | 快照与增量、增删改、任务中断与恢复，对照源库与目标 |
| Module 5.8 | Doris 4.1 CDC_STREAM 目标补丁验证 | 明确模式、源表要求、位点和恢复；不能只凭语法示例验收 |
| Module 5.9 | Streaming Job 对象存储持续文件实验 | 文件发现、处理进度、迟到与补数按目标版本实测；批量 Broker Load 不代替此项 |
| Module 7 | 结合真实 CDC 做位点恢复 | 源端恢复后当前状态与历史逐行对账，明确数据保留窗口和事件版本映射 |

先固定输入、预期和版本，再写演示，最后录制。没有通过运行的路径不得进入录制稿的成功结果。
真实持续接入仍未交付，不能因主线与扩展全部通过而宣布完整 Level 1 已结课验收。
