# Data Warehousing with Apache Doris

本课程围绕订单数仓展开：Level 1 接入、清洗与更新；Level 2 建模、
分析与服务交付；Level 3 权限、资源与运行维护。它与
[Real-time Analytics](../01-real-time-analytics/README.md) 是独立课程，
不会沿用后者的 Level 编号或点击事件数据。

## 当前交付：Level 1 首个 PR 草稿

**这不是完整 Level 1 的发布版本。** 首版包含七个教学单元的讲义、
七个 Lab Notebook、七组互动 Quiz，以及确定性的订单数据。
六个核心 Lab 已在开发实例执行；D04 是需要外部环境的候选实验。
持续接入、对象存储、部分性能演示及正式版本录制仍待补齐。

- [Level 1 学习入口](level1/README.md)：按已确定的顺序学习。
- [环境准备](environments/README.md)：安装、连接和重置范围。
- [数据契约](datasets/README.md)：完整样本、脏数据、事件与独立预期结果。
- [集成实验待办](integration-backlog.md)：明确哪些路径尚未交付。
- [验证记录](VALIDATION.md)：实际执行范围与版本限制。
- [PR 描述草稿](PR_DRAFT.md)：首个 Level 1 材料包的提交说明。

材料语言为中文，Doris SQL 及产品名称保留原文。讲义按总纲的 25 个视频
编号组织，但目前是内容初稿，不是完整逐字稿或已录制视频。

## 安装与运行

从本课程目录操作；只安装本课程即可，Quiz 从同一仓库复用已有渲染器。
不要只复制本课程目录后丢掉相邻的实时分析课程。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

离线测试不连接数据库，检查样本、重放预期、Notebook 格式、Quiz 和辅助代码。
Lab 需要按[环境说明](environments/README.md)设置连接信息及写入确认。

```bash
# 在设置 DW_* 变量之后，执行六个核心 Lab 的原始代码单元。
.venv/bin/python scripts/run_labs.py
# 仅在讲师预置了真实湖表后执行 D04；缺少配置时会报错，不静默跳过。
.venv/bin/python scripts/run_labs.py --iceberg
```

也可使用 JupyterLab 逐单元学习。命令行执行不等于浏览器 UI 验收；
Notebook 不提交执行输出，避免携带本机地址、临时错误 URL 和认证信息。

## 复用与修改边界

复用现有仓库的 Level / Module 组织、讲义 / Lab / Quiz 形式及
`doris_course/quiz.py` 渲染器，不复制其数据源凭据或修改现有运行环境。
新的 `dw_course` 只处理显式连接、数据契约和断言；SQL 留在 Notebook 中，
共用的订单 DDL 在执行前打印。没有新增集群启动器，也不安装 Flink/Kafka。
