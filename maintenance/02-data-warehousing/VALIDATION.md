# Validation record

## 2026-09-20：简化 D01 入门讲义

- 保留 1.1–1.3、五个学习目标及 Lab/Quiz 入口，主线改为认识 Doris、查询订单、按天汇总。
  压缩重复说明，去掉提前展开的 MPP、存算分离、资源组与后续课程能力清单，
  不再在第一课叠加筛选后汇总、HAVING 和重复检测 SQL。
- 保留 FE/BE 分工、基础建表字段、三个查询示例及完整结果，仍提醒重复写入、
  样本范围和订单金额不等于收款。预计时间调整为 50 分钟，是建议值而非试讲计时。
- 不改 Lab、Quiz 或样本；材料测试改为检查保留的结果及其独立样本依据，
  不再要求讲义列出已移除的商品逐行计算表和筛选后日期汇总表。
- 干净 HEAD 副本叠加修改后，63 项离线测试通过；讲义 HTML 的九张表列数一致，
  代码块正常渲染。保留的五条 SQL 与修改前完全相同，本轮未重跑数据库实验。
  本轮只做教学简化，不涉及运行时代码、数据模型、并发或配置变更；学员 Notebook 编辑与输出保留。

## 2026-09-20：补齐示范到独立练习的衔接

- D05 首次 CSV 导入展开 HTTP 请求，逐项说明地址、认证、列映射、请求体和响应；
  后续重试继续复用原 label 与课程封装，独立练习再更换文件、表和映射。
- D05 增加 S3 批量、Kafka Routine Load、MySQL SQL 映射和 S3 持续文件四个阅读示例，
  包含输入、独立目标表、SQL、观测方法和预期结果。按当前官方 4.x 文档核对，
  链接放在对应小节；外部地址及凭据保留占位符，不随 Lab 自动执行。
  新增内容计入建议时间：D05 120 分钟、D06 55 分钟，均未经过真实学员试讲计时。
- D07 在第一个更新例子之前显示实际 DDL，解释 Unique Key、MoW 与 Sequence 属性。
  讲义明确普通字段命名不会自动启用版本裁决。
- D06 抽出唯一性检查，两个预期失败都直接调用它，避免总量检查先失败。
  新反例只替换第二行订单号，行数与总金额保持不变，仍须检出重复；
  每个反例后从分类输入恢复并逐字段验收，再交给 D07。

### 验证与自查

- 干净 HEAD 副本叠加本次变更，63 项离线测试通过。新增五项覆盖首次 HTTP 请求、
  首次展示 Sequence DDL、两个反例的检查入口、总量不变的重复及外部示例边界。
- 使用预设 `scripts/run_labs.py --solutions`，在独立库
  `dw_course_l1_followup_20260920` 执行六个核心 Lab 和六份参考答案，全部通过。
  复用课程单容器，BE 报告 `doris-4.1.3-rc02-7126cf65d96`。
- D05、D06、D07 分别在全新 Jupyter 内核中执行，使用另一独立库
  `dw_course_l1_followup_kernel_20260920`，无 error 或 stderr。
  导出的 HTML 包含两次唯一性检查成功提示，未出现误报的“验收未通过”；答案保持折叠。
- 三份讲义经 Jupyter Markdown 渲染器检查表格列数与代码块，四个外部 SQL 块正常显示。
  最初渲染检查脚本误将高亮代码块定位为 `pre code`，改按渲染器实际的 `pre` 结构检查后通过；
  这不是讲义内容或 Notebook 执行失败。
- 正确性与复用：D06 复用同一个唯一性函数与恢复函数，负例不修改固定预期数据；
  D05 后续请求仍用已有封装，D07 沿用已有 DDL 生成器，没有改变业务模型。
- 范围与兼容性：只修改三单元的讲义、Lab 与对应维护测试；没有新增运行时配置、
  并发、锁、FE/BE 协议或存储格式变更。实验写入仅限上述独立验证库。
  更新的三个 Notebook 的原有输出、执行计数和其他非 source 字段均保持不变；
  课程 01、D01、D02 的用户修改未覆盖或提交。
- 观测与生命周期：HTTP 响应与表内核对都保留；重复反例展示具体订单号。
  外部持续任务示例提供暂停方式，但本次没有实际创建外部任务。
- 未验证：新增外部示例的端到端运行、D04 重跑、浏览器像素级展示和学员完成率。
  真实 S3/Kafka/CDC 集成仍列在 integration-backlog.md，不以文档示例代替集成验收。

## 2026-09-18：Level 1 学习流程与自助实验

- 七个单元保留简洁的功能小节标题，调整模块总标题与时间分配。
  D01 将基础 SQL 单列为阅读小节 D01-03；这次调整不表示新增视频已经录制。
  D05 按单文件、重试、错误批次、历史十表推进；D07 先观察正常更新、重复与乱序，再进入多表中断恢复。
- 七个 Lab 各增加独立任务、空白代码格和默认折叠的参考解答。
  参考答案通过 `run_labs.py --solutions` 单独执行验证；普通 Run All 不会自动完成独立练习。
  七份题库各增加一个新场景，保持五题、四选项、逐选项解释与学习目标对应。
- 修复实验结束时提前关闭连接的问题，连接保持到内核结束。
  常规一致性检查成功时不再重复输出通用绿色卡片，改为展示订单明细、分流原因和业务汇总。
  预期重复错误仅捕获课程结果不匹配异常；没有出现预期错误或发生连接等异常时仍然失败。
- 完整 WWI Parquet 包随仓库提供，压缩后 10,508,150 字节。
  首次使用先在临时目录验证十个文件的大小和 SHA-256，再发布到 `.runtime/wwi/`；已有数据不覆盖。
  Microsoft MIT 许可和来源 manifest 保留；本轮没有上传对象存储或创建远程 PR。
- D04 新增课程专用 MinIO 与 Iceberg REST Catalog，端口 51900/51818 仅监听本机。
  初始化时发现 REST fixture 默认用户无法写入 Docker 命名卷中的 SQLite，修正本地 fixture 用户后启动成功。
  Doris 单容器加入课程湖表网络；按实验库隔离 Catalog/namespace，校验已有 Catalog 的目标与已有样本内容。
  真实 Iceberg 表的直查、关联、导入与重复准备均通过；无须讲师预先提供外部表名。
- 测试平台：Linux x86_64；Doris 镜像标签 `apache/doris:all-in-one-4.1.3`，
  实际构建报告 `doris-4.1.3-rc02-7126cf65d96`。
  MinIO `RELEASE.2025-01-20T14-49-07Z`，Iceberg REST fixture `1.10.0`。
- 暂存区导出的独立目录中 **58 项离线测试通过**：材料结构、35 题四选项交互、
  练习与答案结构、预期错误处理、文件完整性、解包与已有数据保留等。
  工作区存在学员执行输出，因此清洁状态检查在暂存区副本运行；没有清空学员记录。
- 七个 Lab 与七份参考答案在 `dw_course_l1_learning_20260918` 执行通过；
  暂存区副本中再次以 `dw_course_l1_staged_learning_20260918` 全量执行通过，
  未设置 `DW_WWI_DATA_DIR`，从随仓库分发的压缩包完成解包与 701,846 行历史导入。
- 在新 Jupyter 内核执行 D04、D06，输出无 error、无 stderr；
  HTML 包含湖表准备进度、订单结果和“已识别重复订单”，不含误报的“验收未通过”。
  七份 Notebook 导出的 HTML 中参考答案均默认折叠且代码块正常生成。
  原有 14 个 Notebook 的每个既存单元格，输出和非 source 字段的哈希均保持一致。
- 测试库和课程辅助容器保留便于复查；未写入默认学员库、未停止其他项目服务。
  尚未验证：macOS/ARM64 启动、浏览器像素级展示、真实学员独立完成率与试讲时长；
  Kafka/真实 CDC、Group Commit、文件 TVF 等后续实验仍见 [integration-backlog.md](integration-backlog.md)。

## 2026-09-18：Module 1 独立阅读讲义

- 扩充交易与分析场景、Doris 定位、FE/BE 查询路径、表定义解读，以及明细、筛选、
  分组和数据核对的完整示例。保留原有五个学习目标和 Quiz，讲义阅读加 Lab、Quiz 预计 65 分钟。
- 对照 Doris 4.x 官方产品介绍、架构、Duplicate Key、数据类型和 SELECT 文档核对技术说明。
  WWI 订单 4 的三条商品明细、筛选结果及分组金额由本地样本独立核对。
- 在现有课程沙箱的 `dw_course_l1_demo` 上，以只读 SELECT / SHOW 执行全部 10 条讲义 SQL。
  明细、筛选、两种日期汇总、总量和重复检查结果符合讲义；未执行写入或重建表。
  重复 INSERT 后的金额属于模型语义推演，本轮没有实际重复写入学员表。
- 暂存区独立副本上的完整 44 项离线检查通过，新增示例明细和筛选结果与样本一致性检查。
  本轮未修改 Lab/Quiz Notebook，也未重录或修改已有执行输出。

## 2026-09-18：按学习顺序连续编号

- Level 1 改为 Module 1–7：数据质量为 D06，更新与重放为 D07。
  目录、讲义、Lab、Quiz、题库加载路径、跨单元链接和实验执行器同步更新。
- 在暂存区导出的独立副本上，完整 43 项离线检查通过，包含连续编号、
  文件命名、执行器顺序、题库路径、本地链接及 Notebook 语法与清洁状态检查。
- 原工作区 14 个 Notebook 的运行输出及元数据校验保持不变；Jupyter 检查点随目录移动保留。
  本轮仅调整编号和导航，未启动容器、重跑数据库实验或修改业务数据。
- 总纲和 Level 1 实验设计文档同步编号；总纲 13 个单元、50 个视频连续且无重号。
  下方历史验证记录沿用当时编号，映射见[课程维护说明](README.md#编号迁移记录2026-09-18)。

## 2026-09-18：启动进度面板

- 启动流程复用仓库已有的 workflow HTML 和 CSS，显示双列六步卡片、
  进度条、绿勾、红色失败及灰色未执行步骤；标题和状态文字为中文。
- 六步与实际动作对应：Docker / Compose 检查、配置校验、按 missing 策略准备镜像、
  启动并等待容器健康、FE / BE 查询验证、查看课程容器。
  命令输出汇入折叠日志，失败时展开；异常仍向调用方抛出，不继续后续步骤。
- 41 项离线检查通过，覆盖成功、端口占用、下载超时、HTML 转义和原有课程检查。
  保留学员运行记录，未执行要求 Notebook 输出为空的检查。
- 使用新 Jupyter 内核执行真实初始化与启动调用，验证最终输出含六个成功卡片、
  100% 进度及可折叠日志；复用健康的课程容器，未执行表重建。
  验证输出只保存在内存，没有改写学员 Notebook。
- 本轮未执行浏览器截图或像素级视觉比较；布局使用现有共享样式，
  实测范围为 Notebook 输出协议、HTML 内容及真实启动结果。

## 2026-09-18：统一单容器学员启动流程

- D01 去掉已有实例 / Docker 选择与手工连接配置。初始化不启动服务；
  显式执行 prepare_environment(start=True) 才启动课程沙箱，再由 connect_sandbox() 连接。
- 后续六个 Lab 使用相同的 connect_sandbox()，不再启动容器。
  各内核独立配置固定的 FE / BE HTTP 端点，覆盖继承的旧连接地址；
  不需要先导出 DW_ALLOW_WRITES 或让其他 Notebook 继承 D01 的环境。
  执行实验连接单元是对文中重置范围的显式确认，实验库前缀限制仍生效。
- 在 Linux x86_64 上新建并启动 doris-warehousing-course-doris-1，只有一个容器，
  内含一个 FE 和一个 BE；使用项目专属网络、元数据卷和存储卷。
  没有停止、删除或重新配置其他服务。
- 镜像标签为 apache/doris:all-in-one-4.1.3，本机镜像 ID 为
  sha256:5d45eb13bf5e5434c3a2a0fab73ca75a39a5504a6b5ae8dc153efcab60683464；
  FE / BE 实际报告 doris-4.1.3-rc02-7126cf65d96，应保留此构建标识而非仅凭标签推断。
- Compose 健康检查通过；SHOW BACKENDS 显示 Alive=true；
  SELECT SUM(number) FROM numbers("number"="10") 返回 45，验证了 BE 计算。
- 独立库 dw_course_l1_container_20260918 中六个核心 Lab 顺序执行通过，
  包括全部 WWI 文件导入、质量分流、状态重放与业务对账。
  新 Python 进程携带错误的旧连接变量时，仍成功连接课程沙箱并查得
  orders_sample 十行、金额 12220.60。
- 38 项离线检查通过，包括单容器配置、显式启动、健康 / BE 失败分支、
  新内核连接、非实验库拦截、材料及四选项测验检查。
  保留用户 Notebook 执行记录，未运行要求所有输出为空的检查。
- 容器和验证数据保留用于继续学习。BE 报告磁盘使用率约 96.12%，
  可用空间约 78.24 GB，后续应关注空间；本轮没有清理或删除其他数据。
- 未验证：macOS / ARM64 启动、浏览器视觉效果、真实 Iceberg 及其他外部集成。
  Docker 容器日志可按环境说明中的 Compose logs 命令查看。

## 2026-09-18：按业务含义与实验用途命名表

- 七份讲义、七个 Lab、相关测验与环境说明统一使用业务表名；目录和标题仍保留单元编号。
  表名含义见 [Level 1](../../doris-course/02-data-warehousing/level1/README.md#实验表如何命名)。
- WWI 历史表保留 wwi_ 来源前缀；orders_sample、orders_current、order_events 等名称
  表达数据用途。orders_batch / orders_rowwise 等对照表及更新、删除练习表仍独立，
  不因去掉编号而合并实验数据。
- 同步修改动态目标表名与上游引用：质量实验读取 wwi_customers，
  状态实验读取 orders_clean、customers 和 wwi_products；不改数据集、模型或金额口径。
- 32 项离线检查通过，涵盖四选项测验、上游引用、Notebook 语法与本地链接。
  保留学员已有执行记录，因此未运行要求 Notebook 输出为空的检查。
  七个 Lab 的非 source 单元字段与修改前逐项相同，未写入执行输出。
- 通过 scripts/run_labs.py，在新建独立库 dw_course_l1_names_20260918 顺序执行
  六个核心 Lab 两次，全部通过。库中共有 33 个表或视图对象，保留供复查。
  六份核心讲义的 24 条 SELECT / EXPLAIN / SHOW 语句执行通过。
- 验证复用现有 FE 19030 / BE HTTP 18040，没有重启或修改服务配置。
  FE 构建为 doris-0.0.0-ad8644154c3，BE 构建为 doris-0.0.0-8bafeb1e4c4；
  不能据此宣称已验证 4.1.3 发布镜像。
- 本轮没有迁移或删除任何已有学员库、旧版实验表；新版下游需先运行新版上游 Lab。
  未验证 D04 的真实 Iceberg 接入及浏览器视觉效果。

## 2026-09-18：七份讲义的教学内容与课程 01 对齐

本轮完善讲义与测验，数据和 Lab 执行代码沿用下方 WWI 集成版本。
没有增加外部集成环境，不将文档完善视为那些实验已经交付。

### 内容验收

| 单元 | 本轮检查过的讲解与例子 |
| --- | --- |
| D01 | 统一安排表；新增按日期聚合 SQL、两日样本结果与粒度解释；总结补 FE/BE 与第一条查询 |
| D02 | SQL 查询流程、存储层次、写入与 Compaction、受控批次对照、计划/元数据/Profile 的证据区别 |
| D03 | 三次输入与三种模型的逐行输出；逻辑键选择；分区/分桶布局及三个过滤计划 |
| D04 | 文件/表/Catalog 对照、直查与导入选择、完整表名、关联放大与缺失、导入核对；真实环境前置条件保留 |
| D05 | 接入选择表、字段和粒度、CSV/Parquet 映射、请求/响应、重试、对象存储/Kafka/两类 CDC 路径及持续文件概念 |
| D06 | 当前/历史/投递三种粒度、乱序时间线、部分更新前后、删除可见性、中断恢复及独立业务对账 |
| D09-A | 类型与业务规则、十三行输入的去向、拒收回查 SQL、总量与逐行核对、错误注入与新鲜度边界 |

- 七份开头与单元安排采用相同格式；时间分项之和匹配预计时间。
- 每份五个学习目标、五条总结、五道测验逐项核对；题目不再考课程维护待办。
- 作者录制/环境待办保留在 integration-backlog.md；学员仍看到必要的实验限制。
- 官方参考资料按实际主题补齐。检查 29 个不同 Doris 官方入口，修正 TRY_CAST 地址，
  并把产品介绍的旧 meta-refresh 地址改为 getting-started 下的正文地址。

### 本轮验证

- 在干净 HEAD 导出副本叠加本轮材料后，31 项离线测试通过。新增检查覆盖信息表、
  时间安排、目标与测验映射、只读讲义 SQL、WWI 日期结果和模拟事件时间线。
  这些结构检查不代替上表的内容审阅。
- 使用 `scripts/run_labs.py` 在独立库 `dw_course_l1_readings_20260918` 完整执行六个核心 Lab，全部通过。
  复用现有 FE 19030 / BE HTTP 18040，构建版本仍为下方记录的开发构建；没有重启、重配集群。
- 从六份核心讲义的 SQL 代码块提取 24 条 SELECT/EXPLAIN/SHOW 并实际执行，全部通过。
  业务结果与明确预期比较，历史每日汇总另从源 Parquet 独立计算后比对；
  EXPLAIN 和元数据检查执行与返回内容，不断言固定采样版本数或性能倍数。
- 七份讲义通过 Jupyter/nbconvert Markdown 渲染器生成 HTML，检查表格列数、代码块和章节。
- 七套测验分别在全新 Python 内核中执行，并触发每题正确和错误答案的提交按钮：
  核对解释与最终 5/5、0/5 分数，合计 70 次提交。未保存 Notebook 输出。
- 七个 Lab 的代码单元字典与改动前逐项相同（含执行元数据）；只调整其中五个 Lab 的说明文字。
  课程 01 以及用户已有的 Quiz 1 执行记录未清空、未提交。

### 自查与未验证范围

- 目标与范围：覆盖七份讲义、七套测验及相关维护说明；没有改数据契约、运行工具或集群配置。
- 正确性：例子使用现有 WWI/模拟数据，明确粒度与金额；新的只读查询已与实验结果核对。
- 复用与一致性：沿用课程 01 的章节与共享测验渲染器；新增测试延续现有 unittest 风格。
- 测试与结果：离线、真实查询、内核交互和静态 HTML 分别验证，不用结构检查代替端到端证明。
- 并发、生命周期、持久化、FE/BE 参数传递及运行时性能：本轮无相关实现变更；
  实验只写上述独立验证库，不修改现有课程库。
- 未验证：D04 的三条示例查询和真实 Iceberg 接入、S3/Kafka/CDC/Group Commit 扩展实验、
  目标 Doris 4.1.3 发布镜像以及浏览器视觉效果。D04 示例名需替换成讲师提供的实际表名。

## 2026-09-18：Level 1 接入 WWI 与模拟新订单

当前材料以本节记录为准；下方 2026-09-17 内容是旧合成样本的历史验证记录。

- 七份讲义和七个 Lab 已调整数据来源、字段及验收口径，保留课程 01 的模块结构与中文呈现。
- D01–D03 使用 WWI 十单投影，税前金额 12220.60；D04 的候选湖表契约同步更新。
- D05 从本地 Parquet 导入 10 张历史表、701,846 行，核对主键、关联和金额；随后导入模拟新订单 CSV。
- D09-A 使用 WWI 客户维度校验模拟订单：13 行输入、10 行合格、3 行拒收。
- D06 引用同一批客户和商品，覆盖支付、发货、签收、取消、退款；11 笔当前订单、18 条历史、19 次投递。
  独立商品明细、支付、退款、配送流水用于关系与金额核对，支付 250.00、退款 150.00。
- 模拟来源标为 COURSE_SIMULATION，不将 WWI 客户账户收款伪造成逐订单支付。
- 六个核心 Lab 的实际代码单元在独立库 `dw_course_l1_wwi_course_20260918` 顺序运行通过。
  使用下方同一开发 FE/BE，未重启集群，未修改原演示库。
- 同一验证库完整重跑也通过，覆盖实验表重置和重新导入。
- 六个核心 Lab 均在新的 Jupyter 内核中通过，使用独立库 `dw_course_l1_wwi_kernel_20260918`。
  D09-A/D06 改为读取 D05 导入的完整客户、商品维度后，又分别在新内核中通过。
- 27 项离线检查在导出的 Git 暂存区快照上通过，包括历史子集金额/关联、模拟流水对账、
  Parquet 请求参数、缺失/被改写文件拒绝、Notebook 结构、链接和测验定义。
  工作目录只因用户已执行 Quiz 1 的 execution_count 不为空而不满足清洁性检查，未清空用户结果。
- 大文件只放本机忽略目录 `.runtime/wwi/`，与固定 manifest 校验和一致；没有上传桶或提交 Parquet。
- 未验证 D04 Iceberg、S3 TVF、真实 Kafka/CDC、Doris 4.1.3 发布镜像及浏览器视觉效果。
  D05 本地 Stream Load 不冒充最终课程 S3 路径。
- 用户已有的 Quiz 1 执行记录和课程 01 修改保留；不会为清洁性检查清空它们。

复用数据的来源、许可和准备方法见[学员数据说明](../../doris-course/02-data-warehousing/datasets/README.md)，
原始备份实测结果见 [WWI-VALIDATION.md](WWI-VALIDATION.md)。

## Earlier validation history

Date: 2026-09-17. Status: first Level 1 draft, not release-qualified.

## Chinese module structure alignment

Date: 2026-09-17.

- All seven Level 1 readings follow course 01's section order with Chinese
  headings: course information, module goal, learning objectives, module
  structure, topic sections, lab, module summary, quiz and official references.
- Each reading has topic-specific objectives, lab steps and references.
  Quiz links open the interactive notebook rather than the authoring YAML.
- Official reference pages were checked through web access on this date;
  version limitations remain explicit. Suggested lesson times are estimates,
  not measurements from a recorded or trial lesson.
- Removed unrelated ten-million-event claims from the six remaining lab covers,
  replacing them with their actual order-domain goals and environment limits.
- All seven lab code-cell dictionaries (source and execution metadata) were
  compared with HEAD and were unchanged. This was a materials-only revision;
  no new database execution or server restart was needed.
- All 23 offline tests passed on an exported Git index snapshot, including the
  new Chinese section-order check and existing local-link/notebook checks.
  Learner changes to course 01 and the executed course 02 quiz were preserved
  and excluded from the revision.
- Structure alignment does not complete the missing integration labs or bring
  all remaining teaching text to D01's depth; those follow-up tasks remain.

## Database execution

Six core notebooks were executed cell by cell through `maintenance/02-data-warehousing/scripts/run_labs.py`
against an existing, single-node integrated development cluster. The runner
executes the committed Python cells, not an alternate SQL implementation.
The complete core sequence passed twice against the same dedicated database,
including table reset and reinitialization on the second run.
All six core notebooks also passed a third run using real Jupyter kernels
through nbclient, starting each notebook in its own module directory.

- FE: `doris-0.0.0-ad8644154c3`.
- BE: `doris-0.0.0-8bafeb1e4c4`.
- The two components are development builds at different commits.
- Target recording release remains 4.1.3; these results do **not** qualify it.
- A dedicated `dw_course_l1_` database was used. No existing course/business
  tables were reset, and no cluster processes or global settings were changed.

Observed assertions:

| Lab | Evidence |
|---|---|
| D01 | Ten initial orders; order amount 1400.00 |
| D02 | Identical row totals for bulk and individual writes; Tablet metadata inspected |
| D03 | Duplicate/Unique/Aggregate row semantics; partition and bucket plan output |
| D05 | Ten-row Stream Load success; duplicate label rejection without added rows; bad batch rejected |
| D09-A | 12 raw, 10 valid, 2 rejected; full-row comparison; injected duplicate detected and repaired |
| D06 | 11 current orders, 16 historical events; out-of-order and repeat delivery; step-interruption recovery; partial update; isolated soft/SQL deletion |

D06 records one initial interrupted delivery and two seven-delivery attempts,
so the raw delivery table contains 15 rows. Business current/history results
remain unchanged. Paid GMV is 250.00 and refund amount 150.00.

## Validation methods and exclusions

Run offline checks with `python -m unittest discover -s maintenance/02-data-warehousing/tests -v`.
The initial delivery passed 11 tests; structure/presentation alignment passed
18 tests. The D01 learner-facing revision below passes 20 tests.
They validate fixtures against an independent replay calculation, clean notebook
structure and Python syntax, quiz definitions and shared renderer loading,
relative Markdown links, explicit write opt-in and scoped database names,
assertion failure, model DDL generation, and rejection of HTTP redirects.

Notebook outputs remain empty in git. Browser rendering, recorded videos,
Doris 4.1.3, Iceberg and the paths listed in
[integration-backlog.md](integration-backlog.md) are **not verified**.
The small correctness fixtures do not establish performance or resilience.

## Course 01 structure and presentation alignment

- Numbered reading/quiz filenames and single-node environment layout match the
  existing course conventions; Markdown and Notebook links are checked.
- Shared display functions are imported from course 01, not copied into a second
  CSS implementation. Column-name preservation is unit-tested.
- The six core labs passed another cell-runner pass and another real Jupyter
  kernel pass after alignment. Kernel output includes the shared HTML success
  cards and, where queries are displayed, the shared result-table markup.
- All seven quiz notebooks executed in fresh kernels and emitted widget-view
  output. Browser layout and user interaction were not visually inspected.
- Docker Compose configuration validation passed. The locally available pinned
  image contains the expected healthcheck. Startup ordering, explicit opt-in
  and project/port/volume scope were unit-tested with mocks.
- No new container was started or restarted; the new Compose startup path and
  macOS execution remain unverified. Existing-instance mode was used for SQL tests.

Local D01 execution metadata and quiz output were backed up under ignored
`.runtime/alignment-backup/` before editing. Notebook source changes and the
learner's added empty cell were retained; committed outputs remain cleared.

## D01 learner-facing revision

Date: 2026-09-17. This revision expands D01, not every Level 1 reading.

- Course and Level 1 entry pages now lead with prerequisites, learning order
  and links to readings, labs and quizzes. Implementation status is kept in
  maintenance documents, with necessary learner-facing limitations retained.
- D01 explains the order scenario, FE/BE responsibilities, data grain and
  metrics. The lab contains explicit CREATE TABLE and ten-row INSERT SQL,
  expected results, troubleshooting and a read-only filtering exercise.
- Initialization imports tools and styles without connecting. Connection and
  the optional Docker startup are separate, explicitly confirmed steps.
- Removed the unrelated ten-million-event claim from the notebook cover.
- The five quiz questions cover workload fit, result verification, FE/BE,
  order versus paid amount, and Duplicate Key replay behavior.

Validation:

- All 20 offline tests passed against an exported Git index snapshot. The
  working directory still contains the learner's executed Quiz 1 notebook:
  its execution count causes the clean-notebook check to fail there. It was
  neither cleared nor included in this revision. The unrelated course 01
  Untitled notebook was also left untouched.
- Two new tests verify that the visible D01 DDL/INSERT match the shared order
  contract and that initialization is separate from connection/startup.
- The updated D01 notebook passed twice in fresh Jupyter kernels, with its
  module directory as the working directory. SQL checked all ten records,
  1400.00 total order amount, zero paid/refund amounts, EAST=720.00,
  WEST=680.00, and four orders totaling 900.00 for the >=150.00 exercise.
- The D01 quiz executed in a fresh kernel and emitted interactive widget output.
- All six core labs passed the notebook-cell runner after this revision.
- SQL tests used the same development FE/BE builds recorded above and a new
  dedicated database, dw_course_l1_d01_learner_20260917. The learner's existing
  demonstration database and course 01 data were not changed.
- Notebook execution stayed in memory; no generated results were saved into
  course files. HTML result tables and success cards were checked in kernel
  output, but browser layout and interactive clicks were not visually tested.

The Docker lifecycle, target 4.1.3 release, macOS execution and D04 integration
remain unverified. No cluster process was started, stopped or reconfigured.

### Maintainer commands

From the repository root, with the course dependencies installed:

```bash
python -m unittest discover -s maintenance/02-data-warehousing/tests -v
# Configure DW_* and explicitly acknowledge owned-table resets first.
python maintenance/02-data-warehousing/scripts/run_labs.py
# Only when a real Iceberg table is configured:
python maintenance/02-data-warehousing/scripts/run_labs.py --iceberg
```

Use an independent test database. Do not clear a learner's notebook outputs
just to satisfy the clean-source check; validate the staged source separately.

## Learner directory cleanup

Date: 2026-09-17.

- Moved PR/status/backlog documents, the runner and offline tests to
  maintenance/02-data-warehousing at repository level. Updated relative links.
- The runner resolves the course from its own file location and executes each
  notebook from its module directory. It can now run from the repository root.
- All 22 offline tests passed against an exported index snapshot, including
  maintenance separation, link checks and generated-file hiding configuration.
- All six core labs passed using the moved runner and dedicated development
  database dw_course_l1_layout_check_20260917. No learner tables were changed.
- Restarted the localhost Jupyter server with the repository as its file root.
  Its authenticated contents API lists exactly datasets, dw_course,
  environments, level1, README.md, pyproject.toml and requirements.txt under
  course 02. The moved validation document is also accessible through the API.
- Generated installation metadata and caches remain on disk; they are hidden
  from Jupyter's file list. Existing learner notebooks were left unchanged.
- This verifies the file-list response, not a browser screenshot or new
  Docker/target-release qualification.
