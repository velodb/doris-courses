# Level 1 扩展实验

这些 Notebook 补充主线里的独立操作，不新增 Module 或视频，也不代替七个主线 Lab。
按主线顺序先完成 Lab 1、4、5、6、7，再执行扩展；均使用课程单容器沙箱。

| 实验 | 对应单元 | 验收内容 |
|---|---|---|
| [物理设计](extension23_physical_design.ipynb) | Module 2–3 | 100,000 行确定性数据；批次、分区、执行计划和 Profile，结果逐行一致 |
| [文件与合批](extension45_files_and_group_commit.ipynb) | Module 4–5 | 独立 Parquet TVF、INSERT SELECT、Broker Load；默认值与生成列；Group Commit 确认和可见性 |
| [Schema 与删除](extension67_schema_and_delete.ipynb) | Module 6–7 | 加列与类型变更、导入删除后的版本裁决、相同事件 ID 内容冲突检测 |

## 准备与执行

扩展的 Parquet 文件生成需要 PyArrow。从本课程目录安装：

```bash
.venv/bin/python -m pip install -e '.[extensions]'
.venv/bin/jupyter lab level1/extensions/
```

在同一个实验库内一次只运行一个 Notebook。各扩展开头列明会重建的 `ext_*` 表；
不会修改主线业务表，不删除对象存储中的历史文件，也不重启其他集群。
对象存储实验复用课程 MinIO，每次写入独立随机路径。失败后保留任务状态与输出再排查。
建议总计 80–115 分钟，属于补充实验时间，不是视频时长。

维护者可在仓库根目录，用已安装上述依赖的 Python 执行完整回归：

```bash
DW_ALLOW_WRITES=yes DW_DATABASE=dw_course_l1_extensions_check \
  .venv/bin/python maintenance/02-data-warehousing/scripts/run_labs.py \
  --iceberg --solutions --extensions
```

已有主线结果时可改用 `--extensions-only`。脚本不保存 Notebook 执行输出。

## 边界

- 100,000 行与单节点只能提供本地观察，不能证明生产并发、故障恢复或固定性能提升。
- Group Commit 扩展对比单请求确认与查询可见性，不验证多请求共享事务或持续吞吐。
- Schema 变更只覆盖本实验的加列与类型变更，不代表所有模型和变更组合。
- 冲突检测在独立暂存表执行，不是多消费者并发下原子性的拒收服务。
- Kafka、真实源库 CDC、对象存储持续文件任务及真实位点恢复另行交付；本目录不提供这些环境。
