# 模型层实现

## 目标

`code-agent-llm` 为 Agent Runtime 提供与模型厂商无关的异步流式接口，并负责将厂商响应转换成统一事件。模型层不访问 Session、不执行工具，也不依赖 Core 或 CLI。

## 模块

| 模块 | 职责 |
|---|---|
| `provider.py` | `ModelProvider`、取消协议、错误分类和重试装饰器 |
| `registry.py` | 显式注册和按名称获取 Provider |
| `fake.py` | 离线脚本化 Provider，记录请求并回放事件或异常 |
| `providers/openai_compatible.py` | OpenAI Chat Completions 兼容适配器 |
| `types.py` | Provider 无关的消息、请求、响应和流事件 |

## Provider 协议

`ModelProvider.stream(request, cancellation)` 返回 `AsyncIterator[ModelEvent]`。事件包括文本增量、工具调用增量、Usage 和最终完整响应。Runtime 只依赖该协议，因此替换 Provider 不影响 Agent Loop。

Provider 错误统一为 `ModelProviderError`，分类为鉴权、限流、超时、连接、非法请求、畸形响应、取消、服务端和未知错误。错误消息经过清洗，不保留可能泄漏响应内容的异常链。

`RetryingProvider` 仅重试首个事件产生前的可重试错误；一旦输出任何事件便不重试，避免重复文本或工具调用。模型请求、等待下一流块和退避等待都与 `CancellationToken.wait()` 竞速。

## OpenAI Compatible Adapter

适配器完成：

1. 将内部消息和工具 Schema 转换为 Chat Completions 请求。
2. 聚合文本和交错的工具调用参数增量。
3. 保留工具参数原始 JSON，不在模型边界执行 Schema 校验。
4. 归一化 Finish Reason 和 Token Usage。
5. 仅在收到完整 Finish Reason 后发出 `COMPLETED`。
6. 拒绝多 Choice、重复或变化的工具 ID/名称及不完整工具调用。
7. 将 SDK、HTTP 和响应校验错误转换为统一错误类型。

自定义 `base_url` 默认要求 HTTPS并拒绝本地、私网、链路本地和受限制网段。域名型自定义地址还必须加入 `trusted_base_url_hosts`；请求前会再次解析并检查全部地址，内置客户端禁用 HTTP 重定向。确需访问本地模型时必须显式启用对应开关。

API Key 使用 `SecretStr` 保存，不进入日志或序列化配置。Provider Options 不能覆盖模型、消息、工具、流式开关或 Choice 数量等协议字段。

## Fake 与 Registry

`FakeProvider` 接受脚本化事件流或异常，可构造确定性模型交互并记录全部请求。`ProviderRegistry` 使用显式注册，拒绝重复名称和未知 Provider，不依赖导入副作用。

## 测试

默认测试完全离线，覆盖请求转换、文本与工具流聚合、Usage、错误脱敏、EOF 完整性、多 Choice、DNS 私网解析、取消、退避取消、重试边界、Fake 回放和 Registry 冲突。
