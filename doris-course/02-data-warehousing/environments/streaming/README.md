# 选做持续接入环境

两个实验独立选做，不改变七个主线 Lab 的完成条件：

- [Lab 5A：Kafka → Routine Load](../../level1/module05-ingestion/optional5_kafka_routine_load.ipynb)：持续消费、进度、暂停/恢复、重复订单与状态更新。
- [Lab 5B：MySQL → Flink CDC → Doris](../../level1/module05-ingestion/optional5_flink_mysql_cdc.ipynb)：单表快照、增删改、Checkpoint、受控 Savepoint 停止/恢复。Module 7 可复用。

## 前置条件与启动

使用 Linux Docker Engine / Docker Desktop Linux containers、Docker Compose v2，以及课程 Python 依赖。
在 Docker 所在机器运行 Jupyter；远程访问通过 SSH 转发 Jupyter 端口。本配置不是远程 Docker / 生产集群配置。
建议在现有 Doris 沙箱之外预留 4 CPU、6 GB 内存和 8 GB 磁盘；这是准备预算，不是生产容量结论。
第一次运行需要访问 Docker Hub 和 Maven Central 下载镜像与 JAR。

从课程根目录启动 Jupyter，打开上面的 Notebook，顺序执行即可。第一个代码单元明确启动 Doris，随后只启动对应的 Compose profile。
两个实验都复用 `environments/single-node` 的 Doris；不要同时运行同一个实验的两个实例。

| 组件 | 固定版本 / 用途 |
|---|---|
| Doris | 复用 `apache/doris:all-in-one-4.1.3` 单 FE / BE 沙箱 |
| Kafka | `apache/kafka:3.9.0`，单节点 KRaft、单分区演示 |
| MySQL | `mysql:8.0.33`，ROW/FULL Binlog，保留期七天，服务端固定 +08:00，与 CDC 的 Asia/Shanghai 对齐 |
| Flink | `flink:1.20.1-scala_2.12-java11` |
| Flink MySQL CDC SQL JAR | `3.4.0` |
| Flink Doris Connector | `flink-doris-connector-1.20:25.0.0` |
| MySQL JDBC | `8.0.27`，因许可证原因单独下载；见下方 CDC 文档 |

也可以从课程根目录手动启动（先运行 Lab 1 启动 Doris、建立网络）：

```bash
docker compose -f environments/streaming/compose.yml --profile kafka up -d
docker compose -f environments/streaming/compose.yml --profile cdc up -d --build
```

Kafka 与 Doris 共用课程 Docker 网络；Broker 广播 `course-stream-kafka:9092`，生产者在 Kafka 容器内运行。
MySQL 只在 streaming 网络内访问；Flink 同时加入课程 Doris 网络，通过 `doris:8030` 访问 FE，同时显式设置 `benodes=doris:8040`、`auto-redirect=false`，避免单容器沙箱注册的 BE 回环地址被当成 Flink 容器自己的地址。
不要把容器里的 `localhost` 或宿主机映射端口当成服务地址。

宿主机仅新增 `127.0.0.1:51881`（Flink Web UI），Kafka / MySQL 不发布宿主机端口。
演示账号和明文密码只用于本机课程测试；Doris 沿用沙箱 root 空密码，不能照搬至共享或生产环境。

## 数据和恢复边界

- 两个实验使用独立 Doris 库 `dw_course_l1_streaming` 的 `ext_kafka_orders` / `ext_cdc_orders`，仅重建对应表，不修改主线业务表。
- MySQL 初始化脚本仅在首次创建卷时执行；CDC Notebook 每次新实验清空并重新装载专用 `course_cdc.orders`。恢复步骤不清空源和目标。
- Kafka 每次新建随机 Topic 和 Routine Load Job，成功结束会停止任务并删除本次 Topic，保留 Doris 结果。
- Flink 每次新实验使用唯一 label 前缀，恢复时复用相同 SQL / 前缀 / 并行度。Checkpoint 和 Savepoint 存入共享命名卷。
- CDC 实验不经 Kafka；这两条链路不是同一个实验的先后依赖。
- 真实源端变更与受控 Savepoint 恢复是本实验范围；整库同步、Schema 自动演进、崩溃自动恢复、多表原子可见、持续并发不在范围内。
- `SHOW MASTER STATUS` 展示源端末尾，不是 Flink 已消费位点。恢复依赖保存状态、仍可读取的 Binlog 与匹配的作业配置，不能仅保留业务时间戳。

## 排查与停止

从课程根目录查看状态和日志：

```bash
docker compose -f environments/streaming/compose.yml --profile kafka --profile cdc ps
docker compose -f environments/streaming/compose.yml logs --tail=100 kafka mysql jobmanager taskmanager
```

- 出现 `MEM_LIMIT_EXCEEDED`：检查 Docker / 宿主机可用内存及 Doris 的内存水位；先准备充足资源，不要通过关闭内存保护绕过。共享大内存主机可在维护窗口为课程 Doris 配置合理的容器内存上限后重启，保留原有卷。
- Routine Load 超时：查询 `SHOW ROUTINE LOAD`，检查 State、Progress、ReasonOfStateChanged、ErrorLogUrls；确认 Broker 广播地址可从 Doris 访问。
- 上次 Kafka Notebook 中断：在 `dw_course_l1_streaming` 执行 `SHOW ROUTINE LOAD`，核对任务名后 `STOP ROUTINE LOAD FOR <本次课程任务名>`；再运行 Notebook。不要停止其他库的任务。
- Flink 提交失败：读取 SQL Client 输出及 JobManager / TaskManager 日志；确认三个连接器 JAR、网络和源表权限。不能只根据 SQL Client 退出码判断成功。
- CDC Notebook 中断：在 Flink UI 核对课程 Job ID，使用下面的 `flink stop` 保存状态，或者明确放弃该实验后 `flink cancel <job-id>`。新实验初始化会拒绝存在活动作业的环境，避免边同步边清表。
- 恢复超时：检查 Flink 作业异常和 Checkpoint；不要通过删除状态、重跑初始化来伪装恢复成功。若 Binlog 已清理，需要另做重新快照与一致性校验。
- 端口冲突：51881 属于本实验；不要停止其他占用进程。修改 Compose 端口时同步修改 `dw_course/streaming.py` 的 REST 地址。

```bash
docker compose -f environments/streaming/compose.yml exec -T jobmanager \
  /opt/flink/bin/flink stop --savepointPath file:///opt/flink/state/savepoints <job-id>
# 确认 Notebook 已停止本次 Routine Load / Flink 作业后，再停止依赖服务：
docker compose -f environments/streaming/compose.yml --profile kafka --profile cdc stop
```

停止保留所有卷与 Doris 结果。重新 `up -d` 不会自动恢复已经停止的 Flink Job；使用 Notebook 中保存的 SQL 和 Savepoint 路径重新提交。
本课程不自动执行 `down -v`，不自动停止 Doris，也不清理其他项目的容器或卷。

## 维护者验证入口

在仓库根目录，用安装了课程依赖的 Python 运行；日志留在工作区之外：

```bash
DW_ALLOW_WRITES=yes .venv/bin/python \
  maintenance/02-data-warehousing/scripts/run_streaming_labs.py all
```

可将 `all` 替换为 `kafka` 或 `cdc`。脚本执行 Notebook 代码但不保存输出；浏览器 UI 需另行检查。
验证记录见 `maintenance/02-data-warehousing/VALIDATION.md`（仓库根目录）。

## 官方资料

- [Routine Load 语法](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-modification/load-and-export/CREATE-ROUTINE-LOAD/)
- [Flink Doris Connector 版本与配置](https://doris.apache.org/docs/4.x/connection-integration/data-integration/flink-doris-connector/)
- [Flink CDC 3.4 MySQL SQL Connector](https://nightlies.apache.org/flink/flink-cdc-docs-release-3.4/docs/connectors/flink-sources/mysql-cdc/)
