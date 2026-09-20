# 扩展阅读：WWI 发票与账户收款（可选）

[返回 Module 5](course5_batch_and_streaming_ingestion.md)

先完成 Lab 5 的十张历史表导入。本页只查询已有表，不修改数据；
它帮助理解不同金额的口径，不属于批量导入主线的必做步骤。

## 三种金额不要混在一起

| 数据 | 表达什么 | 本课程怎样使用 |
| --- | --- | --- |
| 订单与商品明细 | 下单的数量和单价 | Quantity × UnitPrice 汇总税前订单金额 |
| 发票与发票明细 | 开票记录 | 按发票字段解释，不当成已经收款 |
| 客户账户交易 | 按交易类型记录的账户金额 | 保留原始记账正负方向，不强行逐单分摊 |

## 按交易类型看账户记录

```sql
SELECT t.TransactionTypeName, COUNT(*) AS rows_count,
       SUM(c.TransactionAmount) AS ledger_amount,
       SUM(CASE WHEN c.InvoiceID IS NULL THEN 1 ELSE 0 END) AS no_invoice_link
FROM wwi_customer_transactions c
JOIN wwi_transaction_types t ON c.TransactionTypeID=t.TransactionTypeID
GROUP BY t.TransactionTypeName ORDER BY t.TransactionTypeName;
```

这里 CASE 的含义是：没有关联发票时记 1，否则记 0，再用 SUM 统计数量。
观察收款类型的正负方向，以及 no_invoice_link：本样本中的负数账户收款不能直接当作订单退款，
没有逐单支付关联的数据也不能凭金额强行分摊。模块 6 会进一步练习 CASE 分类。

```sql
SELECT SUM(TransactionAmount) AS ledger_balance,
       SUM(OutstandingBalance) AS outstanding_balance
FROM wwi_customer_transactions;
SELECT COUNT(*) AS delivered_invoices
FROM wwi_invoices WHERE ConfirmedDeliveryTime IS NOT NULL;
```

本课程固定 WWI 包中，第一条查询两列均为 267011.44，第二条为 70426。
这些只是该样本的核对值，不是所有企业都成立的账务等式；账户余额、交付发票数也不是订单支付金额。
模拟新订单的支付和退款使用 Module 7 中独立的业务流水，与这里的 WWI 历史分开。

数据来源、MIT 许可与字段说明见[课程数据说明](../../datasets/README.md)。
