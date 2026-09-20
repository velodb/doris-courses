# Data Warehousing with Apache Doris

从订单数据到可信的业务分析：学习如何接入数据、处理错误记录与状态变化，
再逐步完成数仓建模、分析服务和运行治理。

本课程适合准备使用 Doris 搭建数仓的数据工程师和分析工程师。
你需要能阅读基础 SQL；第一单元会带你完成环境连接和第一张订单表。

## 从这里开始

1. 阅读[环境准备](environments/single-node/README.md)，准备 Python 环境。
2. 打开 [Level 1 学习目录](level1/README.md)，从 Module 1 讲义开始。
3. 在每个单元中依次完成讲义、Lab 和 Quiz。Lab 按单元顺序执行，Quiz 无需数据库。

材料以中文讲解，SQL 和产品名称保留原文。
讲义负责解释“为什么”，Lab 展示“怎么做”，Quiz 帮你检查理解。

## 学习路线

| 阶段 | 你要解决的问题 | 学习成果 |
| --- | --- | --- |
| [Level 1：接入、清洗与更新](level1/README.md) | 数据怎样进入数仓，错误与变化怎样处理？ | 能解释订单输入、质量结果、当前状态和历史 |
| Level 2：建模、分析与服务交付（后续） | 数据怎样组织并交付给业务？ | 分层建模、JOIN、指标加工与看板 |
| Level 3：权限、资源与运行维护（后续） | 多人使用和持续运行怎样管理？ | 访问治理、资源控制和运行维护 |

课程采用 WWI 历史业务数据与独立的模拟新订单，不需要购买数据服务。
Module 1 从仓库内的十笔 WWI 历史订单开始；Module 5 导入完整的 10 表 Parquet 包；
Module 6、Module 7 处理引用同一批客户、商品的新订单，覆盖质量、支付退款和重复乱序。
历史账户收款与模拟订单支付按各自的业务粒度分析。来源、许可与本地数据准备见[数据说明](datasets/README.md)。

## 安装并打开课程

请保留完整仓库，显示和测验功能依赖仓库内的共享组件。
下面从本课程目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/jupyter lab level1/module01-introduction/lab1_connect_and_query.ipynb
```

先启动 Docker，再在 Lab 1 中初始化工具、启动课程单容器沙箱；执行前阅读实验表重置提示。
后续 Lab 自动连接同一个沙箱，不需要重新选择环境或填写连接地址。
密码不要写进 Notebook，具体说明见[环境准备](environments/single-node/README.md)。

## 课程与实验安排

Level 1 提供七个单元，每个单元包含讲义、Lab 和五道交互测验。

| 实验 | 所需环境与数据 |
| --- | --- |
| Module 1–3 | 课程单容器沙箱、仓库内的十单样本 |
| Module 4 | 沙箱；Lab 自动准备的本地 Iceberg 湖表环境 |
| Module 5 | 沙箱、仓库内的历史 Parquet 压缩包与模拟 CSV |
| Module 6–7 | 沙箱及前一单元产出的表、仓库内的模拟事件 |

课程镜像为 apache/doris:all-in-one-4.1.3。Module 5 的 Lab 练习 Stream Load，
讲义同时介绍 Kafka、CDC 与对象存储持续接入的适用场景和工作方式。
Module 4 按需启动两个湖表辅助容器，准备方式见[湖表环境说明](environments/lakehouse/README.md)。

## 维护者资料

[课程维护资料](../../maintenance/02-data-warehousing/README.md)记录环境验证、测试方式与集成实验准备情况。
