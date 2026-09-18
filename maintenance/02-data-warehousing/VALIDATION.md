# Validation record

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
