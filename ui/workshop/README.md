# Workshop UI

Workshop 的独立 UI 工作区。当前为用户选定 A「实验手册」后的设计深化，页面英文，使用 Apache Doris 官方标识。运行页面尚未接入，认证和云资源配置未改。

```text
ui/workshop/
  src/                 页面预览逻辑、样式 token、英文状态文案
  assets/              官方标识、字体与许可证
  contracts/           后续接入使用的展示数据契约草案
  scripts/             只负责 UI 预览构建与浏览器验证
  dist/                生成的独立 HTML，不提交
  review/              设计依据、方向确认、初稿和截图
```

课程 Markdown、Notebook、图片和 manifest 继续由 `doris-course/` 管理。AWS 身份验证、服务归属、代理、销毁及 Terraform 继续由现有运行和管控目录管理。这里不放这些逻辑，也不复制课程源文件。

## 查看与维护

打开 [A 深化原型](dist/prototype.html)。它包含真实十模块阅读内容，可切换 17 个页面或状态，并提供布局密度、字号和登录情形预览。已按用户意见删减入口、首页、目录和工具说明，课程正文保持原样。文件离线可用，顶部评审工具条不属于最终产品。真实登录输入框保持只读，Lab / JupyterLab / Doris 停在明确的外部打开边界。

重建需要 Python 3.11+、PyYAML 和 markdown-it-py，可使用仓库已有课程运行环境：

```bash
.venv-workshop/bin/python ui/workshop/scripts/build-preview.py
node ui/workshop/scripts/verify-preview.cjs
```

浏览器验证使用 Playwright，可设置 `PLAYWRIGHT_MODULE` 与 `PLAYWRIGHT_EXECUTABLE` 指向已有安装。UI 构建只读取仓库内容，不加载 AWS 配置、凭证、账号、Terraform state 或本机任务资料。

独立安装时，在本目录创建 Python venv 并安装 `requirements-preview.txt`，使用 `npm install` 和 `npx playwright install chromium` 准备浏览器，再执行 `npm run build:preview` / `npm run verify:preview`。以上版本取自本轮实际验证环境，不表示各工具最新版本；不需要安装任何AWS SDK。

`dist/prototype.html` 是设计产物，内嵌课程快照便于离线评审；它不是部署包或另一个课程内容来源。修改课程后重新生成。来源与哈希见 [source-manifest.json](review/selected/source-manifest.json)。源码中的英文 UI 状态文案集中在 [copy.json](src/copy.json)，课程正文仍从课程源生成。

## 后续接入边界

建议由现有服务端适配器构造 [展示数据](contracts/view-model.schema.json)，UI 只消费展示模型。路由、账号认证、subject 与服务归属判断继续在服务端；不要由前端根据 learner ID 拼接另一个学员 URL。该 schema 为设计交接草案，尚未接入现有运行时。

运行期课程正文继续由现有 Markdown 渲染和路径白名单提供。UI 样式与未来页面模板在此维护；现有 Python handler 只保留适配与路由，不再追加页面样式长字符串。生产接入优先服务端渲染和同源 CSS，保留现有 CSP；预览脚本不直接复制到门户运行代码。

不提供学习记录、持久成绩、证书或资源管理 UI。唯一 localStorage 数据是设计预览的布局密度和字号，账号、会话、路径、模块和成绩均不写入。

设计规格和状态交接见 [A 设计规范](review/selected/design-system.md)、[页面及状态](review/selected/pages-and-states.md)、[实现交接](review/selected/implementation.md)。方向选择见 [确认记录](review/direction-approved.md)。初稿保留在 [三版比较页](review/index.html)。
