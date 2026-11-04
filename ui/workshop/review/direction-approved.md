# AWS-27.1 视觉方向确认

用户原话：「选A」。

确认范围：选择已展示的 A「实验手册」作为深化方向。用户此前已确认：「沿用英文与 Apache Doris 品牌」。本次方向选择不是最终整套页面评审，也不是运行源码实施、认证迁移或云部署授权。

已展示三版：

- A：[HTML](design-demos/a-lab-manual.html)、[截图](screenshots/a-lab-manual-workspace-1440.png)，Bauhaus Geometric 轮盘 #10，绿色课程主区和编号工具列。
- B：[HTML](design-demos/b-tool-workspace.html)、[截图](screenshots/b-tool-workspace-workspace-1440.png)，工具工作台。
- C：[HTML](design-demos/c-reading-first.html)、[截图](screenshots/c-reading-first-workspace-1440.png)，阅读优先。

三方向已通过真实 HTML、截图及[比较页](index.html)展示。本轮进入同一方向内的深化，不重新发起三方向选择。保留初稿，选定版另存 `selected/`。

## 深化假设

- 保留 A 的内容母题：阅读、运行实验、观察结果；用绿色主区、清楚的编号和行分隔形成层级。
- 字体、布局和状态收敛为一套规范。桌面1440×1000、平板768×1024、手机390×844，并增加320px窄屏验证。
- Cognito 表单由平台托管，仍只表达限定的品牌样式意图；不增加托管页自有导航。
- 仍是本地原型与实施交接，不修改运行页面、认证、安全策略、Terraform或课程源文件。
- 不引入学习记录、持久成绩、结课证书、实例切换或资源管理功能。

## 初稿冻结校验

```json
[
  {
    "path": "design-demos/a-lab-manual.html",
    "sha256": "12cb345af59150aa76821cc8e6c31521d1d0ad245fe3dfae59ab4d3f3e0fd55e"
  },
  {
    "path": "design-demos/b-tool-workspace.html",
    "sha256": "b0fd1c85e24115b5b05966dd1768a5cd7d248d7aa2ec1354e68b5cca93cc20df"
  },
  {
    "path": "design-demos/c-reading-first.html",
    "sha256": "85be033a258197bfecad7f3cac0a0b3bec5b7052ca34185987551a5acdc46722"
  },
  {
    "path": "screenshots/a-lab-manual-workspace-1440.png",
    "sha256": "f776b640a6180172e697e5f8e47aae9d761c9c08e9dfc2033480124bb2010325"
  },
  {
    "path": "screenshots/b-tool-workspace-workspace-1440.png",
    "sha256": "65f877376c30dd06b514173a65c4573ea8279e01d3755c8f41b765ebf87199e1"
  },
  {
    "path": "screenshots/c-reading-first-workspace-1440.png",
    "sha256": "03e263f379bb104976cbfee4522f7c5f037891d4683b995c1c03f98b0731a1c8"
  }
]
```


## 后续目录约束与交付

用户补充原话：「UI 部分需要是独立的目录，方便维护和管理，不要和管控代码以及课程内容混在一起」。已把UI源码、素材、设计与构建验证工具收拢到独立 `ui/workshop/`，本文件随评审资料迁入 `review/`；初稿HTML/截图内容hash不变。管控和课程源未改，预览只读课程并生成忽略提交的HTML。

A深化稿位于 [dist/prototype.html](../dist/prototype.html)，规范位于 [selected/](selected/design-system.md)。方向已经确认，整套设计评审仍待用户查看本轮完整稿。
