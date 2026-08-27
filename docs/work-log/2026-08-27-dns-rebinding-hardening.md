# DNS 解析与公开演示边界加固

## 本轮目标

补齐开放互联网详情页验证中的 DNS rebinding 风险，并校准部署清单的完成状态。此前来源验证能拒绝 URL 中直接出现的私网 IP，但未检查公开主机名解析后的地址。

## 实现

- `SourceVerifier` 在每次详情页请求和重定向后续请求前调用 `socket.getaddrinfo`。
- 解析结果包含环回、私有、链路本地或保留地址时，返回 `private_host/source_untrusted`，请求不会发出。
- DNS 临时解析失败交由 HTTP 客户端继续分类为 `page_unreachable`，避免把网络故障误报成 SSRF。
- 新增回归测试覆盖“公开 ATS 主机解析到 `127.0.0.1`”且断言零请求副作用。
- Streamlit、FastAPI、Docker、Render 和 Streamlit Cloud 的状态边界在部署清单中明确区分。

## 验证

- 先运行新增测试，确认旧实现会实际发起请求并失败。
- 修复后来源验证测试全部通过。
- 全量测试：`398 passed`，`7 warnings`，覆盖率 `90.52%`。
- Ruff 与 Python 编译检查通过。

## 证据边界

该测试证明应用层会在请求前拦截私网 DNS 结果；生产环境仍应配合云平台网络出口策略、 egress firewall 和统一代理，不能将应用层检查表述为完整网络隔离。
