# 初稿品牌与资产清单

用户已确认「沿用英文与 Apache Doris 品牌」。页面英文，与现有课程一致。没有假定公司门户必须冠以 VeloDB 标志。合作伙伴名称未来由受控配置提供，不写死。

| 素材 | 本地文件 | 用法 |
| --- | --- | --- |
| 官方 Doris 横版主标识 | `../assets/doris.svg` | 浅底页头与托管页视觉预览 |
| 官方 Doris 横版深底标识 | `../assets/doris-dark.svg` | B 深色顶部导航 |
| 官方 Jupyter 标识 | `../assets/jupyter.svg` | Notebook 服务名称旁，保持原色和比例 |
| Manrope 可变字体及 OFL | `../assets/manrope.ttf`、`../assets/manrope-OFL.txt` | 导航、工具及标题 |
| Newsreader 可变字体及 OFL | `../assets/newsreader.ttf`、`../assets/newsreader-OFL.txt` | C 阅读与编辑式标题 |
| 仓库 Module 1 真实插图 | `doris-course/01-real-time-analytics/level1/module01-introduction/images/01-fresh-data-to-analytical-answers.png` | 阅读预览中作为内容配图 |

所有 HTML 内嵌以上所需素材，不依赖相对路径。来源下载记录位于 `../assets/sources.json`。Doris 官方 SVG 验证含实际 path 和 `#0dbe85` / `#1d2434` 填色；Jupyter SVG 来自 `jupyter.org`，未重画或改色。工具页不需要产品摄影，真实课程配图仅用于阅读页；没有新增装饰图片。

官方 Doris SVG 自带较大画布留白，直接放入导航会导致字标过小。另保存 `../assets/doris-trimmed.svg` 与 `../assets/doris-dark-trimmed.svg`：仅将 viewBox 收到真实图形包围盒并保留 7 个原始坐标单位的四周空白，保留全部官方路径、比例与颜色；原始文件不改。初稿实际内嵌这两个派生文件。托管登录页字体沿用平台式系统字体示意，不承诺支持上传自定义 Web 字体。

色板：官方绿 `#0dbe85`、墨色 `#1d2434` 为共同锚点。链接与浅底绿色按钮文字使用压低明度的绿色以保证对比度；各方向中性色为本项目设计推导，不冒称品牌规范。不得拉伸、染色或把几何形状当作新 Doris logo。

具名产品清单：Apache Doris、JupyterLab，官方标识已取得。Web Terminal 是功能名称。Cognito/AWS/Carbon/iA 只出现在评审技术说明和设计来源中，不作为页面上的合作或背书品牌。

当前只做页面设计，不改变身份归属、服务原生 UI、认证版本或安全策略。
