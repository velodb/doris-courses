# 实验环境准备

本课程使用官方 All-in-One 镜像，在一个 Docker 容器内运行一个 FE 和一个 BE。
单节点环境用于学习，不是生产部署方案。

## 1. 安装 Python 环境

保留完整仓库，在 `doris-course/02-data-warehousing` 目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

显示和测验功能依赖仓库内的共享组件，请勿单独复制本课程目录。
如果 Jupyter 在远程服务器运行，下文的“本机”指服务器，不是浏览器所在的 Mac。

## 2. 启动课程沙箱

先启动 Docker Desktop（macOS）或 Docker Engine（Linux），并安装 Compose 插件。
建议预留 4 CPU、8 GB 内存和 20 GB 可用磁盘。

直接打开 [Lab 1](../../level1/module01-introduction/lab1_connect_and_query.ipynb)：

```bash
.venv/bin/jupyter lab level1/module01-introduction/lab1_connect_and_query.ipynb
```

先运行“初始化实验工具”，再运行“启动单容器实验环境”中的启动代码。
启动界面显示进度条和双列六步状态卡片：已完成步骤为绿色，当前步骤为黄色，
失败步骤为红色，后续步骤保持灰色。失败时展开完整日志；成功日志默认折叠。
课程工具会校验 [compose.yml](compose.yml)，启动或复用沙箱，等待健康检查，
验证 FE 连接和 BE 计算，然后连接实验库。首次下载和启动可能需要数分钟。

后续 Lab 自动连接同一个沙箱，不再创建容器，也不需要手工设置连接环境变量。
每个 Notebook 都会重新配置同一组连接参数，不依赖其他 Notebook 内核的内存状态。
开始实验前阅读其重置提示；执行连接与实验代码表示你已确认对应教学表可重建。

| 项目 | 配置 |
| --- | --- |
| 镜像 | apache/doris:all-in-one-4.1.3 |
| Compose 项目 | doris-warehousing-course |
| 服务数量 | 一个 doris 服务，容器内包含一个 FE 和一个 BE |
| FE 查询端口 | 宿主机 52030 → 容器 9030 |
| FE HTTP 端口 | 宿主机 51030 → 容器 8030 |
| BE HTTP 端口 | 宿主机 51040 → 容器 8040 |
| 默认实验库 | dw_course_l1_demo |
| 数据保留 | 项目专属 FE 元数据卷、BE 存储卷 |
| 网络暴露 | 只绑定宿主机 127.0.0.1 |

无密码 root 仅用于这个本机教学沙箱，不作为远程部署示例。
本课程使用独立的项目、端口和数据卷，不会复用或停止其他服务的容器。
多人共用同一个沙箱时，可由讲师在启动各自 Jupyter 前设置不同的 DW_DATABASE；
实验库名须以 dw_course_l1_ 开头。实际验证范围见[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)。

## 3. 准备 D05 历史数据包

D01–D03 的 WWI 小样本和 D09-A/D06 的模拟事件已在仓库内。
D05 的完整 Parquet 包暂不从网络下载；请讲师按[数据说明](../../datasets/README.md)准备。
默认放在课程目录的 `.runtime/wwi/`；也可以在启动 Jupyter 前设置：

```bash
export DW_WWI_DATA_DIR=/absolute/path/to/wwi-parquet
```

路径指 Jupyter 内核所在机器。D05 会校验全部文件，不需要 Kaggle 账号、SQL Server 或 S3 密钥。

## 4. 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 导入 Python 包失败 | 确认 Notebook 使用安装课程依赖的 Python 内核 |
| Connection refused | 核对 FE 查询端口，检查服务是否准备好 |
| Access denied | 核对用户、密码、连接来源以及建库建表权限 |
| BE 不存活、无法建表或写入 | 查看 SHOW BACKENDS 和 BE 日志 |
| Docker 端口被占用 | 请讲师协调课程环境；不要停止不属于你的服务 |
| D01 成功而下一个 Lab 无法连接 | 确认内核在同一台机器、沙箱仍在运行；更新课程后重启内核并从初始化重跑 |

排查自己的沙箱时，在课程目录运行：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml ps
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml logs --tail 100 doris
```

## 5. 结束学习与继续学习

暂停自己的沙箱，但保留数据卷：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml stop
```

恢复已创建的沙箱：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml start --wait
```

不要把删除数据卷当作重试手段，也不要操作不属于本课程的容器。

表名按业务含义或实验用途命名，含义见 [Level 1 表名说明](../../level1/README.md#实验表如何命名)。
已有旧版实验表不会自动迁移或删除；使用新版时，请按学习顺序重新运行所需 Lab，
让下游读取新版上游生成的表。

每个 Lab 开头都会说明它重建哪些表。D01 只重建 orders_sample；
D06 读取 D09-A 的合格订单，并只重建自己的订单状态、事件和业务流水实验表。
检查实际 FE/BE 构建版本；开发版本上的结果不能代替正式版本验证。
