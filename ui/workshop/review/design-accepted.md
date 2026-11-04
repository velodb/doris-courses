# 精简 A 版实施基线

用户已选择「选A」、确认英文与 Apache Doris 品牌，并要求独立 UI 目录。完成用户要求的说明文字精简并展示新版后，用户指示：「继续」。

按该指令，以当前精简 A 版收口 AWS-27.1 设计，并进入后续本地 UI 接入。不是逐字段新增确认，也不扩展为云资源创建、认证迁移、push 或 PR 授权。当前设计与课程正文的检查记录见 `selected/verification.json`、`selected/copy-reduction.json`。

后续保持 `ui/workshop/` 独立，控制侧只做可信数据与路由适配。Cognito 仍采用现有托管流程，原生 JupyterLab / Doris 不做深度重绘。
