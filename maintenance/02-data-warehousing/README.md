# Data Warehousing 课程维护

学员入口：[课程首页](../../doris-course/02-data-warehousing/README.md)。
本目录只供课程作者和评审者使用，不是学员的学习步骤。

## 讲义结构约定

沿用课程 01 的章节顺序，先制作中文版本：课程信息表、单元目标、
学习目标、单元安排、分节讲解、动手实验（含数据说明）、单元总结、
知识测验、官方参考资料。保留数仓主题和已定编号，不照搬 01 的数据集。

测验链接指向可交互的 ipynb，YAML 只作为题库源文件。
官方参考资料按本单元主题选择；版本限制和未完成实验需保留明确说明。
结构一致不代表所有单元都已达到 D01 的教学深度或完成版本验证。

| 文件或目录 | 用途 |
| --- | --- |
| [VALIDATION.md](VALIDATION.md) | 实际验证结果与未验证范围 |
| [integration-backlog.md](integration-backlog.md) | 待补齐的集成实验 |
| [PR_DRAFT.md](PR_DRAFT.md) | PR 说明草稿 |
| scripts/run_labs.py | 执行课程 Notebook 的代码单元 |
| tests/test_course.py | 离线检查材料、数据与辅助工具 |

安装课程依赖后，从仓库根目录执行：

```bash
.venv/bin/python -m unittest discover -s maintenance/02-data-warehousing/tests -v
# 先设置 DW_* 参数，使用独立测试库并确认实验表重置范围。
.venv/bin/python maintenance/02-data-warehousing/scripts/run_labs.py
# 仅在预置真实湖表并配置 DW_ICEBERG_ORDERS 后使用：
.venv/bin/python maintenance/02-data-warehousing/scripts/run_labs.py --iceberg
```

如果 Python 环境安装在课程目录，将上面的 .venv/bin/python 换成对应路径。
测试检查待提交 Notebook 不带执行输出；不要为通过检查清空学员的本地记录，
可导出 Git 暂存区到临时目录后验证。

## Jupyter 浏览入口

从仓库根目录启动，01 和 02 使用同一个界面：

```bash
.venv/bin/jupyter lab --config=maintenance/jupyter_lab_config.py --ServerApp.port=18890 --ServerApp.port_retries=0
```

配置以仓库为文件根目录，默认打开 doris-course，确保维护资料的相对链接也可访问。
安装元数据、Python 缓存和 Jupyter 检查点只从文件列表隐藏，不删除；
仍保留默认认证机制。DW_* 连接参数需在启动前设置。
