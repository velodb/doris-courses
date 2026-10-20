# AI Integrations 课程维护

学员入口：[课程首页](../../doris-course/04-ai-integrations/README.md)。
本目录只供课程作者和评审者使用，不是学员的学习步骤。

| 文件 | 用途 |
| --- | --- |
| [module01-design-outline.md](module01-design-outline.md) | Module 1 的中文设计大纲（原 `level1/module01-doris-mcp/readme.md`）。写于课程改为自带环境之前：其中的容器名、端口、数据表、截图计划和文件清单都已过时，以课程目录中的讲义和 Lab 为准 |
| [doris-ai-course-research.md](doris-ai-course-research.md) | Doris AI 课程的前期调研：MCP、Hybrid Search、AI Functions 等方向的能力边界、版本和课程形态建议 |
| [clickhouse-ai-course-research.md](clickhouse-ai-course-research.md) | ClickHouse Academy 中 AI 相关教程的调研，用于对照课程定位 |

## 维护要点

- 课程自带环境：容器 `doris-ai`，端口 39030 / 38030 / 38040（`DORIS_AI_*`），数据 `datasets/events.csv`（由 `datasets/generate_events.py` 按固定种子生成；改动后同步 `datasets/events.yaml` 和讲义中的数字，测试会核对）。
- 讲义 1.6 节的参考记录（reference trace）来自经 launcher 调用 MCP Server 的真实结果，测试用的原始结果在 `tests/fixtures/reference_results.json`。升级 `doris-mcp-server` 或 Doris 后，用 Lab 第 6 节或 `doris_course.mcp_client.run_host_session` 重新取结果，更新讲义和 fixture，并更新讲义末尾的 Tested with 表。
- `doris-mcp-server` 1.0.0 已知问题：`execute_query` 的 `max_rows` 在 2000 左右以上时，对 `ai_course.events` 的 `SELECT *` 返回 `CHILD_RESULT_INVALID`（输出 schema 校验失败，原因被日志脱敏），1000 行正常。讲义只写默认的 100 行上限。
