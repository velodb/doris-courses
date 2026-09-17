# 实验环境准备

本课程使用一个 FE、一个 BE 的存算一体教学环境，或讲师提供的独立实验实例。
单节点环境用于学习，不是生产部署方案。

## 1. 安装 Python 环境

保留完整仓库，在 `doris-course/02-data-warehousing` 目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

本课程复用相邻 01 课程的显示和测验组件，不要只复制 02 目录。
如果 Jupyter 在远程服务器运行，下文的“本机”指服务器，不是浏览器所在的 Mac。

## 2. 选择连接方式

只体验 D01 时，可以直接启动 Jupyter，在
[Lab 1](../../level1/module01-introduction/lab1_connect_and_query.ipynb)
的配置格选择环境并确认允许重建实验表：

```bash
.venv/bin/jupyter lab level1/module01-introduction/lab1_connect_and_query.ipynb
```

**继续其他 Lab 时，建议先按下面的方式配置，再启动 Jupyter。**
Notebook 内设置的环境变量只在当前内核有效，不会自动传给其他 Notebook。
按下面方式启动，可让所有新内核继承同一组连接参数。

### A. 使用讲师提供的实例

将地址和端口改成讲师提供的值；选择自己独立的实验库。

```bash
export DW_START_SANDBOX=no
export DW_HOST=127.0.0.1
export DW_PORT=9030
export DW_BE_HTTP_URL=http://127.0.0.1:8040
export DW_USER=root
export DW_DATABASE=dw_course_l1_demo
export DW_ALLOW_WRITES=yes
.venv/bin/jupyter lab
```

DW_PORT 是 FE 的 MySQL 兼容查询端口。DW_BE_HTTP_URL 是同一集群的 BE HTTP 地址，
供后续 Stream Load 使用，不能填写 FE HTTP 地址。
需要认证时，从进程环境提供 DW_PASSWORD，或在各 Notebook 连接前用 getpass 输入；
不要在 Notebook、Git 或截图中保存密码。

DW_ALLOW_WRITES=yes 表示你已确认各 Lab 的表重置范围，不是数据库权限机制。
请勿连接生产实例或使用他人的实验库。

### B. 使用课程 Docker 沙箱

先启动 Docker Desktop（macOS）或 Docker Engine（Linux），准备 Compose 插件。
资源参考课程 01：4 CPU、8 GB 内存、20 GB 可用磁盘。
镜像固定为 apache/doris:all-in-one-4.1.3。

```bash
export DW_START_SANDBOX=yes
export DW_HOST=127.0.0.1
export DW_PORT=52030
export DW_BE_HTTP_URL=http://127.0.0.1:51040
export DW_USER=root
export DW_PASSWORD=
export DW_DATABASE=dw_course_l1_demo
export DW_ALLOW_WRITES=yes
.venv/bin/jupyter lab
```

运行 D01 的“选择实验环境”和“启动（可选）并连接”单元。
它会校验 [compose.yml](compose.yml)，启动课程项目，等待健康检查并执行 SELECT 1。
首次下载和启动需要数分钟。目标版本与启动路径的实际测试范围见[验证记录](../../../../maintenance/02-data-warehousing/VALIDATION.md)，
当前 Docker 启动尚未端到端实测。

| 项目 | 配置 |
| --- | --- |
| Compose 项目 | doris-warehousing-course |
| FE 查询端口 | 宿主机 52030 → 容器 9030 |
| FE HTTP 端口 | 宿主机 51030 → 容器 8030 |
| BE HTTP 端口 | 宿主机 51040 → 容器 8040 |
| 数据保留 | 项目专属 FE 元数据卷、BE 存储卷 |
| 网络暴露 | 只绑定宿主机 127.0.0.1 |

无密码 root 仅用于这个本机教学沙箱，不作为远程部署示例。
课程 02 的项目、端口和卷与课程 01 分开，不会复用或停止课程 01 的容器。

## 3. 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 导入 Python 包失败 | 确认 Notebook 使用安装课程依赖的 Python 内核 |
| Connection refused | 核对 FE 查询端口，检查服务是否准备好 |
| Access denied | 核对用户、密码、连接来源以及建库建表权限 |
| BE 不存活、无法建表或写入 | 查看 SHOW BACKENDS 和 BE 日志 |
| Docker 端口被占用 | 请讲师协调端口或改用已有实例；不要停止不属于你的服务 |
| D01 成功而下一个 Lab 无法连接 | 按上面的环境变量方式重启 Jupyter，并重启旧内核 |

排查自己的沙箱时，在课程目录运行：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml ps
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml logs --tail 100 doris
```

## 4. 结束学习与继续学习

暂停自己的沙箱，但保留数据卷：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml stop
```

恢复已创建的沙箱：

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml start --wait
```

不要把删除数据卷当作重试手段。连接已有实例时，不要执行服务管理操作。

每个 Lab 开头都会说明它重建哪些表。D01 只重建 d01_orders；
D06 读取 D09-A 的合格订单，并只重建自己的 D06 表。
检查实际 FE/BE 构建版本；开发版本上的结果不能代替正式版本验证。
