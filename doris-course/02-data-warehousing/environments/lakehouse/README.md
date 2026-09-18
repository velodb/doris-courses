# 湖表实验环境

Lab 4 使用课程 Doris 查询真实 Iceberg 表。运行准备单元格会启动两个辅助容器：

| 服务 | 固定镜像 | 本机端口 | 保存内容 |
| --- | --- | --- | --- |
| MinIO | minio/minio:RELEASE.2025-01-20T14-49-07Z | 51900 | Iceberg 元数据文件和 Parquet 数据 |
| Iceberg REST Catalog | apache/iceberg-rest-fixture:1.10.0 | 51818 | SQLite 表目录 |

先完成 Lab 1，安装课程 requirements.txt 中的依赖，确认两个端口空闲。
在 Lab 4 中依次运行代码即可；准备过程会展示进度并核对十笔订单。
首次运行需要网络下载镜像。之后复用命名卷中的数据，并检查样本是否符合课程清单。

辅助容器与课程 Doris 加入同一 Docker 网络。每个实验库使用独立的 Catalog 和湖表命名空间，
准备步骤保留已有表；样本内容不一致时停止并报告差异。

这些服务用于本地教学：端口仅绑定 127.0.0.1，凭据公开写在 compose.yml 中。
REST fixture 以 root 身份运行，以便写入 Docker 命名卷中的 SQLite；生产环境需要另外设计身份和访问管理。

## 停止与再次使用

从本课程目录运行以下命令停止辅助服务，命名卷中的数据保留：

```bash
docker compose -f environments/lakehouse/compose.yml stop
```

下次运行 Lab 4 的准备单元格会重新启动。此命令不停止 Doris。
连接错误时先检查 Docker 状态、端口占用，再查看课程辅助服务日志：

```bash
docker compose -f environments/lakehouse/compose.yml ps
docker compose -f environments/lakehouse/compose.yml logs --tail 80 rest minio
```
