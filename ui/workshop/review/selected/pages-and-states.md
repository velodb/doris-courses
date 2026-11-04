# A 页面与状态交接

所有产品文案为英文。顶部中文工具条、示例状态选择和原生工具边界说明只属于评审原型，生产页面不包含它们。

## 页面与返回路径

| 页面 | 用户看到什么 | 主操作与返回 |
| --- | --- | --- |
| Activities | 来自 active registry 的活动；一句组织者账号提示 | Sign in to workshop → 活动自己的认证入口 |
| Hosted sign-in | 平台纵向表单和官方标识 | 平台成功回调进入本人服务页；首次登录/可选MFA由真实挑战决定 |
| First password | 平台要求的新密码字段和当前策略提示 | 平台处理，不在自有 UI 存密码 |
| My workspace | 绿色课程主区、本人归属、仅分配到的服务 | Open course；工具分别打开；退出当前入口 |
| Course outline | manifest 的十模块标题、level与操作 | Read lesson → 阅读；Open Lab → 本人 JupyterLab |
| Lesson 1–10 | 完整源 Markdown、图片、表格、代码与目录 | 当前 Lab；课程目录；下一课。下一课不等于完成记录 |
| Web Terminal guide | 当前真实操作：JupyterLab → File → New → Terminal | Open JupyterLab；My workspace |
| External boundary | 仅原型显示工具名和目标路径，说明真实环境应独立打开 | Back to lesson；My workspace。生产直接跳转至服务，不产生该中间页 |

三种可演示登录情形：首次改密 → 本人页、已有账号 → 本人页、首次改密 → MFA → 本人页。它们仅用于展示，不决定真实账户流程。字段只读，没有认证请求。

## 状态文案与恢复动作

权威 UI 状态文案集中维护于 `src/copy.json`；这里说明触发条件与实施语义，不另外复制维护全部文案。

| 状态 | 标题 | 触发和展示边界 | 主操作 |
| --- | --- | --- | --- |
| 无活动 | No active workshops | active registry 为空；不要暴露已结束活动历史或其他账号信息 | Check activities |
| 活动结束 | This workshop has ended | 由可信活动期限决定；不得据任意403猜测环境被删除 | Back to activities |
| 服务未就绪 | Your environment is not ready | 对本人授权服务的不可用响应；说明等待和联系组织者，不承诺自动重建 | Try opening again |
| 无权限 | Access unavailable | 403或无有效 assignment；保持通用，不显示目标实例/其他学员身份 | Back to activities |
| 会话到期 | Sign in to continue | 当前受保护入口需要重新认证；别把正常首次访问当异常 | Sign in again |
| 入口退出 | Signed out of this entry | 完成原POST、Cookie处理和Cognito logout后的落地页 | Back to activities |
| 账号帮助 | Contact your organizer | 说明组织者发放账号、过期临时密码的admin-only恢复 | Back to activities |
| 登录失败 | Sign in / Incorrect username or password. Try again. | 具体真实文案以Cognito为准，不披露账号是否存在 | 平台内重试 |

结束页提示课程文件可能已不可用；未就绪页可显示本人活动/学员ID帮助报告问题；无权限和公开状态不带个人身份。退出页说明其他服务标签可能有自己的会话，提示关闭，不承诺全站即时撤销。

## 目录及数据

UI 根目录为 `ui/workshop/`。课程源位于 `doris-course/`，运行及管控位于现有 `workshop/aws/`、`tools/`。构建预览可以读取课程，但不修改和另存课程源文件。生成的 HTML 在 `dist/`，忽略提交；课程仍只有一份事实来源。

当前活动/个人服务数据来自可信服务端视图；模块来自 manifest；正文由既有安全 Markdown 渲染结果提供。展示数据契约草案位于 `contracts/view-model.schema.json`，不把 UI schema 当作认证或授权检查。

新增活动人类名称、讲师联系人、起始时间或实时健康不是本轮必须字段。缺失时回退到真实 event ID；没有联系人就说明找组织者，不编造邮箱或在线客服链接。

## 最终评审需要确认的内容

用户已选 A 并确认英文和 Doris 品牌。此次深化覆盖实际页面、内容和状态，需用户查看完整稿后完成设计评审；不能把前一轮的方向选择代替这次整套验收。页面运行接入和部署仍另行安排。
