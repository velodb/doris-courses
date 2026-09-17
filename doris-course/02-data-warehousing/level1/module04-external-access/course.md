# D04：湖表与内部表关联

本讲义服务于 Data Warehousing with Apache Doris Level 1。版本：首版草稿；已实现与待补实验分别标明。

[课程入口](../README.md) · [动手实验](lab4_query_iceberg.ipynb) · [知识测验](quiz.yaml)

## 学习目标

读完后能解释本单元的业务问题，在 Lab 中核对证据，并说清尚未验证的边界。先阅读数据契约，再运行实验，不将未执行的部分当作完成。

## D04-01：内部表、外部文件与湖表查询

业务已有历史数据时，先探索数据、判断是否需要导入比默认全量迁移更合适。内部表由 Doris 管理数据；文件 TVF 直接访问文件；外部 Catalog 对接已有系统的表元数据。

Parquet 是文件格式，Iceberg 是带元数据管理的表格式，文件目录不自动成为 Iceberg 表。通过 Catalog 关联内部客户表后，需要检查维表键唯一性，防止订单金额被重复累加。

**演示安排：** 候选 Lab 对预置 Iceberg 表进行直查、关联与导入后对账。没有外部服务时不能运行；独立文件 TVF 部分仍待补。

## 复习与实验检查

完成本单元五道测验；对照 Notebook 的预期结果，保存实际运行版本、业务结果与失败原因。测验不要求数据库连接，Lab 不能由测验成绩替代。

参考：[Apache Doris 文档](https://doris.apache.org/docs/4.x/gettingStarted/what-is-apache-doris/)；实现与验证范围以[课程状态](../../README.md)为准。
