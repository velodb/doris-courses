# 课程数据：WWI 历史业务 + 模拟新订单

本课程使用 WWI 历史业务与模拟新订单，贯穿历史导入、质量校验和状态更新。

| 数据 | 来源及用途 | 口径 |
| --- | --- | --- |
| [wwi/sample.json](wwi/sample.json) | WWI 十笔历史订单及关联维度；D01–D04 | 税前订单金额 12220.60，不表示收款 |
| WWI 全量核心 Parquet | D05：10 张表、701,846 行，约 16.2 MiB | 原始订单、明细、客户、商品、发票、客户账款及字典 |
| orders.json / orders.csv | 课程生成的十笔新订单；D05、D06 | 初始金额 1400.00，支付和退款均为零 |
| raw_orders.json | 10 笔正常新订单 + 非法金额、空订单号、无效客户各一行 | 13 输入 → 10 合格、3 拒收 |
| malformed_orders.csv | 一行正常、一行非法金额 | D05 严格模式整批拒绝 |
| deliveries.json | 新订单状态的九次投递，含八个不同事件 | 乱序、重复和中断恢复；不是 Binlog |
| business_events.json | 独立列出的商品明细、支付、退款、配送事件 | 用于对账，不由被测 Notebook 的当前表生成 |
| expected_current.json / expected_summary.json | 固定预期明细与汇总 | 不在 Lab 中根据实际输出改写 |

## WWI 来源与转换

原始数据是 [Microsoft Wide World Importers v1.0](https://github.com/microsoft/sql-server-samples/releases/tag/wide-world-importers-v1.0)，
微软生成的模拟批发企业数据库。
保留[原始 MIT 许可](wwi/LICENSE.txt)；[manifest](wwi/manifest.json)记录备份哈希、表与字段、文件哈希和行数。

历史日期保持 2013–2016，不平移成今天。核心 Parquet 保留所选表的全部行，只选择课程所需字段；
datetime2 的第七位小数明确截断到微秒，计算列导出为值。
十单样本选择订单 1–5、80–84；order_amount 为明细 Quantity × UnitPrice 汇总，
line_count 为明细行数，额外投影字段 data_source=WWI。它不是完整两天的营业额。
保留这些订单引用的客户和商品，并附上模拟新订单需要的客户、商品 1–11。

WWI 客户收款是账户层的记录，26,637 条收款的 InvoiceID 为空；
不能分摊成逐订单支付，也不能把负数收款当成退款。这份备份没有贷项发票业务样本。

## 本地准备完整 Parquet 包

D01–D03 与模拟事件的小文件随仓库提供。D05 使用讲师分发的完整 Parquet 包，
包含 manifest 所列的十个文件。请向讲师取得数据包，并按下方步骤放到内核所在机器。
D06、D07 继续读取 D05 导入的客户和商品维度，因此应先完成 D05，再进入质量与状态实验。

取得数据包后，在仓库根目录运行，将示例路径替换为包所在目录：

```bash
.venv/bin/python maintenance/02-data-warehousing/scripts/prepare_wwi.py \
  --source /absolute/path/to/wwi-parquet
```

脚本校验十个文件后复制到本课程的 `.runtime/wwi/`。目标目录若已有同名文件，脚本会停止并提示使用新目录，以保留现有数据。
或者在启动 Jupyter 前将 DW_WWI_DATA_DIR 设为包所在目录；路径是内核所在机器的路径。
Lab 直接读取本地 Parquet，按课程清单检查文件完整性。
缺失文件时 D05 在重建表之前停止。

从官方备份重建作者导出包的步骤见[WWI 验证记录](../../../maintenance/02-data-warehousing/WWI-VALIDATION.md)。
由于 Parquet 编码可能随工具版本变化，发布新包时需同时更新 manifest，不能忽略校验失败。
向学员分发包时附带本说明、manifest 和微软许可。

## 模拟新订单契约

- 来源为 COURSE_SIMULATION，订单号 900001–900011，与 WWI 历史分开。
- 初始 10 单引用 WWI 客户/商品 1–10；第 11 单引用客户/商品 11。
- 价格、EAST/WEST 地区和 2026 年事件是教学设定，不代表 WWI 的真实地理属性或历史。
- 模拟事件使用 Asia/Shanghai；固定质量截止时间为 2026-01-02 12:00:00，不表示实际接入新鲜度。
- event_version 是每个订单递增的教学版本，不是 Binlog 位点或时间戳。
- 同 event_id 的重投具有相同业务字段。delivery_id 是投递编号，不是物流编号。
- 初始快照保留 S1001…S1010 事件 ID，订单 ID 独立为 900001…900010；两种标识不要求相等。

900001 经支付、发货、签收到 DELIVERED，900003 经支付、取消、退款到 REFUNDED；
900002 未支付取消，其他订单保持 CREATED。投递故意先发签收事件，考察乱序裁决。

重放后：11 笔当前订单、金额 1510.00，18 条逻辑历史；累计支付 250.00、退款 150.00，
净收款 100.00。中断前一次投递加两轮九次投递，共 19 次原始投递。
支付、退款、配送和商品明细另存业务表，重复写入按各自稳定 ID 去重。
模拟事件以本地文件提供，由 Lab 按给定次序写入，练习版本裁决与重放对账。

模拟订单 CSV 列顺序：

```text
order_id,customer_id,order_amount,status,event_version,event_id,event_time,paid_amount,refund_amount,region,data_source
```
