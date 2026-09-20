# Module 6：数据质量与 Schema 校验

| 课程信息 | 内容 |
| --- | --- |
| 所属课程 | Data Warehousing with Apache Doris · Level 1 |
| 产品版本 | Apache Doris 4.x |
| 实验版本 | Apache Doris 4.1.3 |
| 预计时间 | 约 65 分钟，包含讲义阅读、动手实验和测验 |

[课程目录](../README.md) · [打开实验 6](lab6_validate_orders.ipynb) · [打开测验 6](quiz6_data_quality_and_rejection.ipynb)

## 单元目标

本单元介绍数据类型校验与业务质量规则，以及如何保留和处理不合格记录。

完成本单元后，你将能够将订单数据分为合格和拒收记录，追踪拒收原因，并用已知错误验证检查规则。

## 学习目标

完成本单元后，你应该能够：

1. 区分字段类型校验与业务质量规则。
2. 保留输入标识和原始字段，追踪每条拒收记录。
3. 利用独立客户维度和明确规则分流合格与拒收数据。
4. 同时核对输入覆盖、业务唯一性、金额和逐行结果。
5. 用已知错误验证检查会失败，并区分离线时间口径与实际新鲜度。

## 单元安排

| 环节 | 学习形式 | 建议时间 | 学习成果 |
| --- | --- | --- | --- |
| 6.1 Schema 与业务质量 | 字段、结构变更与规则对照 | 10 分钟 | 说明类型正确为何不等于业务正确 |
| 6.2 暂存与拒收 | 分类 SQL 与分流结果 | 13 分钟 | 追踪原始字段和每条拒收原因 |
| 6.3 质量测试与验收 | 错误注入案例 | 7 分钟 | 证明检查能发现已知错误 |
| 实验 6 | 动手操作 | 30 分钟 | 完成 13→10＋3 分流，生成 Module 7 合格输入 |
| 测验 6 | 交互测验 | 5 分钟 | 检查规则、来源、分流、对账和校验有效性 |

## 6.1 Schema 与业务质量

### 两道不同的检查

Module 5 已经说明：接口接受了数据，不表示它能直接进入业务报表。
本节先全部保留原始输入，再按业务规则分流。

| 输入 | 类型检查 | 业务检查 |
| --- | --- | --- |
| 金额 not-a-number | 不能转成 DECIMAL | 不允许作为正常金额进入合格表 |
| 订单号为空 | 无法取得有效整数标识 | 无法识别订单，应拒收 |
| 客户号 999999 | 可以转成整数 | 不在客户维度中，仍应拒收 |
| 金额为负数 | 可以是合法小数 | 是否允许要由业务契约决定，本样本不允许 |

Doris 表字段约束、导入严格模式与课程 SQL 质量规则是不同层次。
维表关联等业务检查由本课显式 SQL 实现，不是声明一个字段类型就自动完成。

### 先固定本批数据契约

数据契约是上游与下游对字段含义和合格条件的共同约定。
例如本批输入都是“新建订单”，所以必须有订单号，金额应非负，客户必须已登记。
把这些约定写成检查规则，下游才能知道哪些数据可用于分析，哪些需要退回处理。

合格数据必须有可用订单号、非负金额、有效客户引用，并且来源是
COURSE_SIMULATION。客户维度来自 Module 5 已导入的 WWI 客户表，不能从当前输入
临时生成“客户名单”，否则错误客户也会被当成有效。

字段结构变化时，应重新检查列映射、类型和业务规则。
例如上游把金额改成含税金额，即使字段仍为小数，原来的税前汇总口径也需要调整。
本 Lab 将这份契约落实为 SQL 分类规则与结果检查。

### 字段变了，原来的检查还能用吗？

Schema 校验检查“这一批输入是否符合表结构”；Schema Change 则修改表结构本身。
例如新增可空的 `order_channel`，旧数据可以暂时未知，但导入映射和下游 SELECT 都应检查；
把金额从税前改成含税，类型即使没变，也必须重新约定口径。

| 变更方式 | 主要工作 | 本节需要理解的影响 |
| --- | --- | --- |
| Lightweight Schema Change | 支持的操作只修改元数据，不重写已有数据文件 | 如符合条件的新增值列；仍需检查默认值、列映射和下游兼容性 |
| Heavyweight Schema Change | 在后台转换或重写数据文件 | 某些类型或列顺序变更；需要关注任务状态、资源与转换结果 |

不能只凭“加列”“改类型”几个字判断所有表都适用同一路径，要结合目标版本和表模型。
下面是独立副本上的操作形状，不在 orders_clean 上执行：

```text
ALTER TABLE <独立副本表> ADD COLUMN order_channel VARCHAR(20) NULL;
DESC <独立副本表>;
```

执行前先检查旧写入是否显式指定列；执行后核对表定义、旧行的新列值，以及新批次导入。
涉及后台转换时，用 `SHOW ALTER TABLE COLUMN` 查看任务状态，再核对转换后的数据，
不要把提交成功当作转换完成。完整发布与回滚在 Level 3 的 Module 12 学习。
本 Lab 保持字段不变，专注数据准入。[Schema Change](https://doris.apache.org/docs/4.x/table-design/schema-change/)

## 6.2 暂存与拒收

### 让每条错误都有来处

原始层 `orders_raw` 把业务字段先保存为字符串，另加稳定的 input_id。
原始值不因转换失败被丢弃，后续可以解释哪一行、哪个字段出了问题。

这里需要两种编号：`input_id` 标识这次收到的某条输入，`order_id` 标识业务订单。
即使输入缺失订单号，仍能凭 input_id 找到原始记录；同一订单重复到达时，
也可以用不同 input_id 留下每次输入的痕迹。

| input_id | order_id 原始值 | order_amount 原始值 | customer_id | 预期去向 |
| ---: | --- | --- | --- | --- |
| 1–10 | 900001–900010 | 合法金额，共 1400.00 | 1–10 | 合格 |
| 11 | 900012 | not-a-number | 1 | INVALID_AMOUNT |
| 12 | NULL | 100.00 | 1 | INVALID_ORDER_ID |
| 13 | 900013 | 100.00 | 999999 | INVALID_CUSTOMER |

`TRY_CAST` 尝试转换字段类型，无法转换时返回 NULL，便于在分流规则中识别错误。
例如 `TRY_CAST('not-a-number' AS DECIMAL(18, 2))` 得到 NULL，
分类规则将对应输入标记为 INVALID_AMOUNT；合法金额通过这一项检查。
原始文本继续留在 orders_raw，方便修正后重新处理。
转换行为见[CAST 与 TRY_CAST](https://doris.apache.org/docs/4.x/sql-manual/basic-element/sql-data-types/conversion/cast-expr/)。
规则按顺序选取第一项拒收原因：订单号 → 金额 → 客户引用 → 来源。
一行同时有多处问题时，先记录第一个命中原因；修正并重新处理后，再判断剩余问题。

### 用 CASE 把规则写成 SQL

CASE WHEN 按从上到下的顺序检查条件，THEN 给出命中结果；全部不命中时取 ELSE。
下列视图保留原始字段，只增加 reject_reason。TRY_CAST 用于判断是否合法；
真正写入合格表时，目标列类型再约束保存的值。

**SQL 阅读示例：对应 Lab 6 的分类步骤，不要在已完成的 Lab 上重复执行。**
先在 Lab 中准备 orders_raw 和独立 customers 维表：

<!-- reading-only-example -->
```sql
CREATE VIEW orders_classified AS
SELECT *, CASE
    WHEN TRY_CAST(order_id AS BIGINT) IS NULL THEN 'INVALID_ORDER_ID'
    WHEN TRY_CAST(order_amount AS DECIMAL(12,2)) IS NULL
      OR TRY_CAST(order_amount AS DECIMAL(12,2)) < 0 THEN 'INVALID_AMOUNT'
    WHEN TRY_CAST(customer_id AS BIGINT) IS NULL THEN 'INVALID_CUSTOMER'
    WHEN TRY_CAST(customer_id AS BIGINT) NOT IN (SELECT customer_id FROM customers) THEN 'INVALID_CUSTOMER'
    WHEN data_source IS NULL OR data_source <> 'COURSE_SIMULATION' THEN 'INVALID_SOURCE'
    ELSE NULL END AS reject_reason
FROM orders_raw;
```

本例 customers.customer_id 不允许为空；不要直接把 NOT IN 套到可能含 NULL 的客户集合。
input_id=11 先通过订单号检查，再命中金额规则；input_id=13 的金额合法，
但客户引用不存在，因此命中 INVALID_CUSTOMER。十条正常输入的 reject_reason 为 NULL。

`orders_classified` 是保存分类 SQL 的普通视图，查询它时会执行转换检查和分类判断。
视图为每条输入计算 reject_reason，后续写入步骤再把原因为空的记录送入合格表，
把有原因的记录送入拒收表。分流由 Lab 中两条显式写入完成；仅创建视图不会自动搬运数据。

```text
13 行原始输入 orders_raw
          │ 与独立客户维度检查
          ▼
分类视图 orders_classified（带 reject_reason）
          ├─ 原因为空 → orders_clean：10 行
          └─ 原因非空 → orders_rejected：3 行，保留 input_id
```

### 两个 WHERE 决定写到哪里

**SQL 阅读示例：对应 Lab 6 的分流步骤，不要在已完成的 Lab 上重复执行。**
Lab 先创建空的 orders_clean、orders_rejected，再执行以下两条写入。
合格表使用 Duplicate Key；重复执行会追加数据，不是更新已有分流结果。

<!-- reading-only-example -->
```sql
INSERT INTO orders_clean (
    order_id,customer_id,order_amount,status,event_version,event_id,
    event_time,paid_amount,refund_amount,region,data_source
)
SELECT order_id,customer_id,order_amount,status,event_version,event_id,
       event_time,paid_amount,refund_amount,region,data_source
FROM orders_classified WHERE reject_reason IS NULL;

INSERT INTO orders_rejected (input_id, reason)
SELECT input_id, reject_reason
FROM orders_classified WHERE reject_reason IS NOT NULL;
```

IS NULL 与 IS NOT NULL 将同一次分类结果分成互斥的两组：十行合格、三行拒收。
这两次写入不是自动的跨表原子事务；在两步之间中断时，不能把第一步成功当作全部完成。
本 Lab 保留静态原始输入，重做时按 Lab 初始化顺序重新建立结果表，再完成两步并核对。

学会 CASE 后，可以先写一条只读 SELECT 试算新规则，再决定是否用于正式分流。
Lab 的独立练习会让你增加金额复核规则，但不改动供 Module 7 使用的合格表。

### 从拒收记录回到原始字段

完成 Lab 的分流步骤后执行：

```sql
SELECT r.input_id, r.order_id, r.order_amount, r.customer_id, x.reason
FROM orders_rejected x
JOIN orders_raw r ON x.input_id = r.input_id
ORDER BY r.input_id;
```

结果应对应上表 11、12、13 三行。拒收表保存编号与原因，原始表保存原文，
两者通过 input_id 关联。临时 ErrorURL 不代替这套长期可查的业务记录。

客户号转换成功但引用不存在，同样不能进入合格表。
来源为空或不是 COURSE_SIMULATION 也会拒收，不过当前十三行样本没有额外的来源错误行。

## 6.3 质量测试与验收

### 总数守恒只是第一步

```sql
SELECT reject_reason, COUNT(*) AS input_rows
FROM orders_classified
GROUP BY reject_reason
ORDER BY reject_reason;
```

预期：原因为空十行，三个拒收原因各一行。需要同时保证覆盖与不重叠，
不能让一条输入既进入合格表又进入拒收表，也不能漏掉一条输入。

```sql
SELECT COUNT(*) AS orders,
       COUNT(DISTINCT order_id) AS unique_orders,
       SUM(order_amount) AS amount
FROM orders_clean;
```

查询结果应为 10、10、1400.00。接下来，逐条比较订单字段，检查明细是否与原始输入和业务规则一致。
明细检查可以发现汇总中被掩盖的差异，例如两笔错误金额一增一减、总额保持不变。

| 检查层次 | 本实验验证什么 |
| --- | --- |
| 输入覆盖 | 13 条均有去向，合格 10、拒收 3 |
| 标识与关系 | 订单号不重复，客户存在，来源正确 |
| 金额与状态 | 初始总额 1400.00，状态 CREATED，支付/退款为零 |
| 时间口径 | 事件时间非空且不超过固定业务截止时刻 |
| 完整记录 | 全部字段与独立样本逐行一致 |

### 故意制造错误，验证检查确实有用

Lab 用两个反例单独验证订单号唯一性：先追加一笔已存在的订单，再在恢复后，
把第二行的订单号改成第一行的订单号。第二个反例保留全部金额，仍为十行、1400.00，
却包含重复订单号，并遗漏原来的第二笔订单。

两个反例都直接调用唯一性检查，避免总量检查先报错、掩盖唯一性规则是否有效。
每次先显示重复的订单号，再确认唯一性检查失败，最后从保留的分类输入恢复并运行全部检查。

```text
正确输入 → 检查通过
追加重复 / 总量不变的重复 → 唯一性检查失败（预期）
恢复数据 → 检查再次通过 → 交给 Module 7
```

预期结果来自固定输入和业务规则，恢复操作则从保留的原始数据重新分类。
这样即使目标表被误写，也有独立依据判断恢复是否正确。

固定截止时刻 `2026-01-02 12:00:00` 用于检查本批离线样本的事件时间。
持续接入场景还需要同时记录业务发生时间、接入时间和观测时间，
才能区分业务迟到、传输延迟和处理积压。

## 动手实验 6：保留输入、分流拒收、自动验收

开始前请完成 Module 5，了解导入响应与业务质量的区别，并使用课程独立实验库。

打开[实验 6](lab6_validate_orders.ipynb)，按顺序完成：

1. 暂存十三行输入，包括非法金额、缺失订单号与无效客户。
2. 显式分流为十行合格、三行拒收，按 input_id 回查原因。
3. 逐行核对合格记录，注入重复订单验证检查失败，再恢复正确数据。

### 数据来源与说明

历史部分采用 Microsoft WWI；新订单及变更标为 COURSE_SIMULATION，引用 WWI 客户和商品，但不回填历史。
字段、业务口径和预期结果见[数据说明](../../datasets/README.md)。

## 单元总结

- 类型校验回答能否解析，业务规则回答是否应该进入报表；客户号是整数不代表客户真实存在。
- 保留 input_id 和原始文本，拒收原因才能回查；错误日志不能自动变成业务拒收表。
- 使用 Module 5 的独立客户维度，按订单号、金额、客户、来源规则分流；本样本是 13 输入、10 合格、3 拒收。
- 总量、唯一性、关系、金额和逐字段比较相互补充；一个总数不能证明数据完整正确。
- 已知重复必须使检查失败，恢复后再通过；固定截止时间不是实际运行中的新鲜度指标。

## 知识测验 6：保留输入、分流拒收、自动验收

完成讲义和实验后，打开[测验 6](quiz6_data_quality_and_rejection.ipynb)。
测验包含五道单选题，不依赖 Doris 或外部服务；提交后阅读答案解释。

## 官方参考资料

- [Stream Load：类型转换和质量参数](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)
- [CAST 与 TRY_CAST](https://doris.apache.org/docs/4.x/sql-manual/basic-element/sql-data-types/conversion/cast-expr/)
- [Schema Change](https://doris.apache.org/docs/4.x/table-design/schema-change/)
