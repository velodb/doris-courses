# 已核实事实与实现边界

核对日期：2026-11-04。源码基线 `aws@60ad53d`。

- 自有门户公共活动列表只输出未过期的 registry 活动；个人入口从已验证 subject 的 assignment 中取得服务链接。见 `workshop/aws/access/portal.py:107` 及 `:126`。
- 课程阅读、Notebook、终端引导均在个人 Jupyter origin；终端需要在 JupyterLab 中选择 File → New → Terminal。见 `workshop/aws/runtime/portal.py`。原生工具独立打开，不能宣称已有跨 origin 统一导航。
- Terraform 未显式配置 `managed_login_version`，也没有品牌资源。现有已验收界面是默认托管页；当前无活跃活动，因此本轮不报告对线上域名版本的实时查询。实施前显式核对版本，初稿保守采用 classic 可定制边界。
- Classic 托管 UI 支持居中 logo 和限定 CSS 类/属性；不支持任意布局或自有脚本。logo 上传为 PNG/JPG/JPEG，建议不大于 100KB，CSS 不大于 3KB，总请求不大于 135KB。[AWS 官方说明](https://docs.aws.amazon.com/cognito/latest/developerguide/hosted-ui-classic-branding.html)。SVG 在自有页面内嵌可用；未来托管页应单独转换成合规 PNG，当前不生成或上传认证配置。
- Managed login 与 classic 是不同域名品牌版本；Lite 仅支持 classic。升级版本涉及配置和重新验证，初稿不默认采用。[AWS 官方说明](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-managed-login.html)。
- 账号仅由管理员创建，恢复为 `admin_only`，密码最低 14 字符且含大小写、数字、符号，临时密码有效 1 天；MFA 为 OPTIONAL，支持软件 TOTP。依据 `workshop/aws/terraform/access-bindings/main.tf:36`。不设计自助注册、邮箱找回或主动选择别人的实例。
- 当前入口退出仅清理当前 Host 的 ALB Cookie 并经过该活动 Cognito logout；不能把它描述为所有服务标签或既有连接的全局撤销。设计提示用户关闭其他服务标签。
- rta-main 课程有十个模块，Module 1 名称为 Introduction to Apache Doris。课程声明版本 4.1.3 是仓库内容事实，不是本轮声称的 Doris 最新版。
- 官方品牌资产来源为 [Apache Doris Brand Assets](https://doris.apache.org/brand-assets/)。主标识颜色实取 `#0dbe85` 与 `#1d2434`。
- B 的布局参照：[Carbon typography strategies](https://www.carbondesignsystem.com/building-blocks/foundations/typography/style-strategies)，仅迁移任务导向密度，不宣称获得其设计或授权。
- C 的思考锚点：[iA Writer](https://ia.net/writer/)，仅借鉴聚焦阅读的取舍；设计为本项目自行提出。

没有 AWS API、认证请求、新资源或云端验收。初稿中的身份与可用状态全部是评审示例。
