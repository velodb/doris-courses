# UI 接入与维护

UI 源码独立于管控和课程。现有门户和课程 handler 已接入服务端渲染器；本轮完成本地运行验证，尚未部署 AWS。

| 文件 | 职责 |
| --- | --- |
| `doris_workshop_ui.py` | 展示渲染、文字/URL转义和固定静态资源表；只消费可信路由与已清洗正文 |
| `src/templates/` | 页面骨架与各页面模板 |
| `src/styles.css` | A版共享样式和响应式token |
| `src/copy.json` | 状态文案 |
| `src/services.json` | 服务ID的展示名称与分组，不决定访问权限 |
| `package.py` | UI专用确定性归档，不打包原型、测试、课程或管控代码 |
| `install_assets.py` | 门户安装时按固定URL与SHA256准备字体/Jupyter标识；请求渲染不联网 |
| `scripts/build-preview.py` | 只读课程源生成离线设计预览 |
| `scripts/verify-integrated.cjs` | 检查真实本地HTTP页面、CSP和响应式布局 |

`workshop/aws/access/portal.py` 继续验证Host、活动、签名subject和assignment，之后构造已分配链接并调用UI。`workshop/aws/runtime/portal.py` 继续验证Jupyter认证和课程路径，传入现有安全Markdown渲染结果。UI不导入AWS SDK，不读取Terraform state、身份配置或本机任务资料。

当前使用Python函数调用传递展示模型；`contracts/view-model.schema.json` 保留为后续独立API的设计草案，不是当前新建的HTTP接口。

## 资源、CSP和退出

共享门户只在已配置apex的 `/doris-courses/static/` 提供四项公开资源：CSS、Doris标识、Jupyter标识和字体。个人页及退出页使用同一apex资源，避免退出后资源请求再次触发认证。仅这些固定公开资源返回CORS头；活动/个人数据不开放CORS。

自有页面没有内联脚本，CSS也使用固定资源地址；不启用 `unsafe-inline` 或 `unsafe-eval`。课程表格对齐改为CSS类，保持原正文含义并满足严格CSP。表格和代码有可聚焦滚动区。

Terraform中只有精确的门户退出落地路径改为转发门户，其余学习服务的退出仍返回原固定文本。退出请求仍要求签名身份、同Origin和POST；不承诺全站会话同步撤销。Cognito托管表单与认证版本未改，登录页品牌化仍需在后续真实环境中单独应用和验证。

## 打包与安装

门户和学员runtime均增加hash绑定的 `ui.tar`。学员包离线包含完整字体和图片；门户包保留官方资源的固定URL与hash，在安装时校验下载。运行页面无CDN依赖。

门户SSM安装分UI暂存和主体安装两段，各段继续小于24KB。每段绑定目标实例与payload hash，并保留自己的dispatch receipt；未知派发结果、失败或输入变化均要求对账，不自动重发。旧安装记录缺少新operation metadata时拒绝复用。

UI在目标机安装到独立的 `ui/` 目录，由root拥有。归档拒绝路径越界、链接、特殊文件和重复项。新bundle含UI且重新计算runtime hash；既有AMI、旧runtime、TTL binding和runner镜像没有被改写或重标。使用新UI需要构建新包/新镜像并完成对应真实验收。

## 本地验证

```bash
python3 -m unittest discover -s ui/workshop/tests -q
GIT_NO_LAZY_FETCH=1 .venv-workshop/bin/python -m unittest discover -s tools/tests -q
.venv-workshop/bin/python tools/tests/run_ui_browser.py
```

浏览器脚本使用项目Playwright或 `PLAYWRIGHT_MODULE` / `PLAYWRIGHT_EXECUTABLE` 指向的安装。fixture只绑定loopback，以临时测试签名和本地Jupyter token连接真实handler，结束后回收进程和凭证。它不验证真实ALB/Cognito、云端浏览器、网络/IAM或部署，不能替代新活动的端到端验收。
