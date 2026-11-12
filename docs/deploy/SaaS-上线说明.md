# SaaS 上线说明（账号 / 试用 / 额度 / 付费 / 团队）

## 1. 现在的形态

```
落地页  https://shelby982.github.io/tech-solution-generator/
   ├ 登录      → /login
   └ 申请试用   → /register（注册即开通 14 天试用）
应用    https://tech-solution-generator.onrender.com/
   ├ /upgrade   套餐与订单
   ├ /team      团队协作（邀请成员）
   ├ /forgot    找回密码（发信或后台发件箱取链接）
   ├ /reset     设置新密码
   └ /admin     运营后台（需 ADMIN_TOKEN）
```
未登录访问 `/`、`/three-mode`、`/review`、`/project-intake`、`/upgrade`、`/team` → 307 到 `/login`。

## 2. 关键数据表

| 表 | 用途 |
|---|---|
| `accounts` | 账号（email / scrypt 密码 / plan / trial_ends_at / token_limit / status）|
| `account_sessions` | 会话（Cookie `ge_session`，httpOnly）|
| `billing_orders` | 订单（plan / 金额 / 状态 / 通道 / 标记付款时间）|
| `email_tokens` | 验证 / 重置 / 邀请令牌（带过期与已用标记）|
| `mail_outbox` | 发件箱（未配发信通道时把邮件内容与链接落库）|
| `team_members` | 团队成员（owner / member / 角色 / 状态 / 邀请令牌）|
| `projects.owner_account_id` | 项目归属（团队成员共享 owner 的项目）|

## 3. 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `SAAS_MODE` | 开启账号体系（1/0）；**托管平台自动开启**（检测 `RENDER`/`RAILWAY_ENVIRONMENT`）| 本地关 |
| `SAAS_TRIAL_DAYS` | 试用天数 | 14 |
| `SAAS_TRIAL_TOKEN_LIMIT` | 试用期 token 上限 | 2,000,000 |
| `SAAS_USAGE_WINDOW_DAYS` | 用量统计窗口 | 30 |
| `SAAS_SESSION_DAYS` | 会话有效期 | 30 |
| `ADMIN_TOKEN` | 运营后台口令；**不设则后台关闭** | 空 |
| `STRIPE_SECRET_KEY` | 有则走 Stripe Checkout；无则人工开通 | 空 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_FROM` | 邮件通道（SMTP）| 空 |
| `RESEND_API_KEY` | 邮件通道（Resend）| 空 |
| `CONFIG_PATH` | 持久盘目录（库放 `<CONFIG_PATH>/ge_solution.db`）| 空 |

> **注意**：Render **免费实例不能挂持久盘** → 每次重新部署数据会清空。
> 需要长期保存数据请升级付费实例（挂 Disk）或改用托管 Postgres。

## 4. 上线后要做的三件事

1. **在 Render 后台加 `ADMIN_TOKEN`**（强口令）→ `/admin` 可用：
   看账号与用量、**手动延期**、改套餐/额度、**停用账号**、看订单、看发件箱。
2. （可选）`STRIPE_SECRET_KEY` → 升级走在线支付；不配则订单进后台人工开通。
3. （可选）邮件通道：配 `RESEND_API_KEY` 或 SMTP → 验证/重置/邀请自动发信；
   不配时邮件落到**发件箱**，可在后台复制链接手动发给用户。

## 5. 业务流程

### 试用
注册 → 14 天试用 + 200 万 token（近 30 天）→ 到期后仍可登录看数据，但不能再建项目；
额度用完后生成/评审类写接口返回 **429**（新建项目仍允许）。

### 升级
`/upgrade` 选套餐 → 下单（pending）→
- 有 Stripe：跳转支付，成功后自动升级；
- 无 Stripe：运营在后台「标记已付款」→ 自动 `activate()`（升级 plan + 延长到期 + 提升额度）。

### 团队
`/team` 邀请成员：
- 邮箱已注册 → 立即加入（管理员后台可见）；
- 未注册 → 生成 14 天邀请链接（发信或发件箱取），注册后接受即加入；
- 席位按套餐（trial/pro = 1，team = 10），超限 403 提示升级；
- 成员可看/协作 **owner 的项目**（项目归属仍记在 owner 名下）。

### 找回密码
`/forgot` → 生成 2 小时有效重置链接（不泄露邮箱是否存在）→ `/reset` 设置新密码 →
**踢掉该账号所有旧会话**，需重新登录。

## 6. 安全要点

- 密码 `hashlib.scrypt` + 每账号 salt；比对用 `hmac.compare_digest`
- 会话 token 随机 32 字节，httpOnly + SameSite=Lax；`SAAS_COOKIE_SECURE=1` 可强制 Secure（https）
- 管理接口必须带 `X-Admin-Token`，未配置时整体 404（不会误开放）
- 试用额度闸门只拦"会烧 token 的写接口"，不影响只读与建项目
- 账号隔离：项目列表按"自己 + 团队 owner"过滤；停用账号无法登录
