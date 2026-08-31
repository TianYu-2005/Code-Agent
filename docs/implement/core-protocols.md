# 核心协议实现

## 范围

核心协议分布在 `code-agent-llm` 与 `code-agent-core`：

- `code_agent_llm.types`：消息、模型请求、模型响应、流事件和用量。
- `code_agent_core.tools`：工具规格、影响类型和有界结果。
- `code_agent_core.runtime`：运行预算、运行描述和执行上下文。
- `code_agent_core.events`：带关联 ID 的版本化运行事件。
- `code_agent_core.session`：树状会话节点及类型化 Payload。

## 实现方式

协议使用 Pydantic v2 严格模型：禁止额外字段和非有限浮点数，实例冻结，嵌套 JSON 容器进行防御性复制和深度冻结。持久化协议携带 `schema_version`，时间统一转换为 UTC。

模型协议不包含 Provider SDK 类型。`ToolCall.arguments_json` 保存模型返回的原始参数字符串，参数解析和 JSON Schema 校验由工具执行层负责，使畸形模型输出能够转换为工具错误，而不是破坏模型流。

`RuntimeEvent` 统一携带 `session_id`、`run_id` 及可选的 Turn、Model Call、Tool Call 关联 ID，并按事件类型校验必需关联字段。`SessionEntry` 从第一版采用 `id + parent_id` 树结构，Payload 按消息、运行事件、压缩和记忆分别建模。

## 边界约束

- `code-agent-llm` 不依赖 Core 或 CLI。
- `code-agent-core` 只向下依赖 LLM。
- 协议对象不负责网络、存储、工具执行或用户交互。
- `ToolResult` 同时限制正文字符数和整体序列化字节数。
- `ExecutionContext` 包含运行时能力句柄，因此不参与序列化。

## 测试

测试覆盖严格校验、不可变性、JSON 往返、消息与工具调用不变式、事件关联、Session Payload、大小限制、时间戳和源码依赖方向。
