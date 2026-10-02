# OpenAI Operation SDK

独立维护的 OpenAI / ChatGPT 操作适配 SDK，**不是 OpenAI 官方 SDK**。由原 `team-operation-sdk` 扩展而来，保留原仓库历史。

## 模块划分

```text
openai_operation_sdk/
  team/       # Team 工作区、成员、席位、邀请、撤销和接受邀请
  accounts/   # 账号身份、安全设置、TOTP 注册/激活/移除及动态码生成
  core/       # 共用代理锁定、浏览器指纹、错误类型、JWT 元数据读取
team_operation_sdk/  # 旧 Python 导入路径的兼容层，无重复协议实现
```

新增业务按独立模块扩展；各业务模块依赖 `core`，不依赖 Team 管理项目、数据库或其他业务模块。新登录授权仍由独立的 credential-session-kit 管理；CPA/Sub2API 导入导出仍由独立的 importer 管理，本仓库不复制这些项目。

## 代理、指纹与凭据

- 显式传入代理；不使用系统环境代理，不回退直连。
- 同一客户端固定代理和 `curl_cffi` 浏览器指纹，默认 `safari18_0`，可通过 `impersonate` 参数指定。
- 指纹组件缺失时明确报错，不降级为普通 HTTP 会话。
- SDK 不保存账号密码、2FA 密钥和 Token，不包含商家数据库、队列或租户权限。
- 账号安全接口先校验 Session 邮箱与目标账号一致，不使用母号凭据代替。
- 远端写入不自动重试；不确定结果通过结构化错误的 `uncertain` 标记交由上层处理。
- `core.tokens` 只解析 JWT 元数据，不做签名验证；身份必须由远端接口确认。

## 使用示例

```python
from openai_operation_sdk.team import TeamClient
from openai_operation_sdk.accounts import AccountSecurityClient
from openai_operation_sdk.core import OpenAIOperationError

with TeamClient(
    workspace_id="WORKSPACE_ID", admin_email="owner@example.com",
    access_token="ACCESS_TOKEN", session_token="SESSION_TOKEN",
    proxy_url="socks5://user:pass@proxy.example:443",
) as team:
    snapshot = team.snapshot()  # 新命名空间返回 dict；保留时间、容量与总数字段

with AccountSecurityClient(
    email="account@example.com", session_token="ACCOUNT_SESSION",
    proxy_url="socks5://user:pass@proxy.example:443",
) as account:
    account.authenticate()
    factors = account.mfa_info()
```

Team 模块还提供 `invite`、`remove`、`revoke_invite(invite_id, email)`。候补侧 `CandidateClient` 提供 `accept_invite`、`accept_and_exchange`、`export_workspaces` 和 `reauthorize`。

## 2FA 的责任边界

`accounts.AccountSecurityClient` 提供 `authenticate`、`mfa_info`、`totp_factors`、`enroll_totp`、`activate_totp`、`disable_totp`；`accounts.generate_totp` 生成六位动态码。

调用方负责：明确选择账号、业务权限与母号保护、并发控制、加密暂存新密钥、激活前持久化、激活读回、新凭据登录验证、移除旧因子、数据库更新和失败恢复。SDK 不提供会偷偷扩大为全部账号的批量入口。

若远端拒绝替代因子注册、要求重新认证或响应不明确，停止该账号操作并保留恢复信息。模拟响应测试不代表真实账号的 2FA 变更已通过端到端验收。

## 迁移

- GitHub 仓库：`WangSiangCun/openai-operation-sdk`
- 发行包：`openai-operation-sdk`（0.2.0）
- 新 Python 命名空间：`openai_operation_sdk`
- 旧 `team_operation_sdk` 导入仍由薄兼容层提供；旧 `TeamClient.snapshot()` 保留 dataclass 返回格式，旧 `revoke_invite(email)` 保留单参数签名。
- 原 Git 提交仍保留，已固定旧提交的应用不必立即升级。新项目请使用新名字并固定完整 Git 提交。
- `team-operation-sdk` 与 `openai-operation-sdk` 两个发行版包含兼容命名空间，迁移现有环境时先卸载旧发行版再安装新发行版，或使用干净虚拟环境。不要在新包安装后再卸载旧包，以免误删共享路径。

## 验证

```shell
python -m pip install -e .
python -m pytest -q
```

SDK 测试覆盖模块边界、代理锁定、指纹必需、账号身份检查、2FA 写入失败、邀请/席位读回及旧导入兼容。没有测试默认访问真实账号。
