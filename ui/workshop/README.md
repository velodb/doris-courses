# Workshop UI

Workshop 的独立 UI 工作区。采用用户选定的精简 A「实验手册」，页面英文，使用 Apache Doris 官方标识。自有门户与课程页面已完成本地运行接入；尚未部署 AWS，Cognito 托管认证版本未改。验证结果见 [接入状态](integration-status.md)。

```text
ui/workshop/
  src/                 服务端模板、样式、文案、服务展示名称及原型逻辑
  assets/              官方标识、字体与许可证
  contracts/           后续接入使用的展示数据契约草案
  scripts/             UI 预览构建与浏览器验证
  doris_workshop_ui.py  纯展示渲染器
  package.py           确定性 UI 安装归档
  install_assets.py    安装期官方静态资源校验
  dist/                生成的独立 HTML，不提交
  review/              设计依据、方向确认、初稿和截图
```

课程 Markdown、Notebook、图片和 manifest 继续由 `doris-course/` 管理。AWS 身份验证、服务归属、代理、销毁及 Terraform 继续由现有运行和管控目录管理。这里不放这些逻辑，也不复制课程源文件。

## 查看与维护

先运行下方构建命令，再打开生成的 `dist/prototype.html`。`dist/`、历史初稿和截图仅保留在本机，不包含在干净 checkout 中。它包含真实十模块阅读内容，可切换 17 个页面或状态，并提供布局密度、字号和登录情形预览。已按用户意见删减入口、首页、目录和工具说明，课程正文保持原样。文件离线可用，顶部评审工具条不属于最终产品。真实登录输入框保持只读，Lab / JupyterLab / Doris 停在明确的外部打开边界。

重建需要 Python 3.11+、PyYAML 和 markdown-it-py，可使用仓库已有课程运行环境：

```bash
.venv-workshop/bin/python ui/workshop/scripts/build-preview.py
node ui/workshop/scripts/verify-preview.cjs
```

浏览器验证使用 Playwright，可设置 `PLAYWRIGHT_MODULE` 与 `PLAYWRIGHT_EXECUTABLE` 指向已有安装。UI 构建只读取仓库内容，不加载 AWS 配置、凭证、账号、Terraform state 或本机任务资料。

独立安装时，在本目录创建 Python venv 并安装 `requirements-preview.txt`，使用 `npm install` 和 `npx playwright install chromium` 准备浏览器，再执行 `npm run build:preview` / `npm run verify:preview`。以上版本取自本轮实际验证环境，不表示各工具最新版本；不需要安装任何AWS SDK。

`dist/prototype.html` 是设计产物，内嵌课程快照便于离线评审；它不是部署包或另一个课程内容来源。修改课程后重新生成。来源与哈希见 [source-manifest.json](review/selected/source-manifest.json)。源码中的英文 UI 状态文案集中在 [copy.json](src/copy.json)，课程正文仍从课程源生成。

## 已接入的运行边界

现有门户和课程handler通过薄适配调用独立渲染器，UI只消费已验证的展示数据。账号认证、subject与服务归属继续在服务端，前端不根据learner ID拼接资源URL。[展示schema](contracts/view-model.schema.json)仍是后续独立API的草案，当前通过Python函数传递数据。

运行期课程正文继续由现有 Markdown 渲染和路径白名单提供。UI样式与页面模板在此维护；Python handler保留路由、校验与数据适配。实际页面使用服务端渲染与固定CSS资源，无内联脚本；原型脚本不进入运行安装包。资源策略、归档与SSM分阶段安装见 [接入说明](architecture.md)。

不提供学习记录、持久成绩、证书或资源管理 UI。唯一 localStorage 数据是设计预览的布局密度和字号，账号、会话、路径、模块和成绩均不写入。

设计规格和状态交接见 [A 设计规范](review/selected/design-system.md)、[页面及状态](review/selected/pages-and-states.md)、[实现交接](review/selected/implementation.md)。方向选择见 [确认记录](review/direction-approved.md)。初稿保留在 三版比较页 `review/index.html`。
