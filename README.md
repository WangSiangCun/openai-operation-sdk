# Team Operation SDK

独立封装 Team 席位相关的读写操作，供 Team 管理项目复用。

## 特性

- 所有请求固定经过调用方传入的 HTTP/HTTPS/SOCKS5 代理，不回退直连
- 使用 `curl_cffi` 的 Safari 浏览器指纹（`safari18_0`）发送请求
- 禁用环境代理，检测代理配置被篡改
- 先交换并校验工作区 access token，再执行读写
- 支持读取席位、分页、邀请、撤销邀请、移除成员
- 支持候补账号在同一代理和指纹会话中接受邀请
- 不保存账号密码、2FA 或凭证；凭证生命周期由上层项目管理

## 使用

```python
from team_operation_sdk import TeamClient

client = TeamClient(
    workspace_id="WORKSPACE_ID",
    admin_email="admin@example.com",
    access_token="AT",
    session_token="SESSION",
    proxy_url="socks5://user:pass@proxy.example:443",
)
try:
    snapshot = client.snapshot()
    print(snapshot.members, snapshot.invites)
    client.invite("member@example.com", "prolite")
    client.remove("REMOTE_MEMBER_ID")
finally:
    client.close()
```

`session_token` 存在时，SDK 会通过同一代理交换目标工作区 token，并校验返回身份；所有后续请求继续使用同一代理和同一指纹会话。

## 本地检查

```powershell
python -m pip install -e .
python -m pytest -q
```
