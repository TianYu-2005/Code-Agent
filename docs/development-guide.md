# Code Agent 开发指导

本文说明项目的增量开发方式。完整功能和模块定义见 `design.md`。

## 1. 开发原则

1. **先协议，后实现**：先定义数据类型和 `Protocol`，再编写具体模块。
2. **先闭环，后扩展**：优先跑通“模型调用—工具执行—结果反馈”，再增加高级能力。
3. **逐模块集成**：每完成一个模块，立即接入最小 Agent Loop 并运行测试。
4. **依赖抽象**：Runtime 只依赖协议，不直接依赖模型 SDK、CLI、文件系统或 MCP。
5. **保留最终结构**：各阶段可使用简化实现，但接口和持久化结构始终遵循最终设计。
6. **安全入口唯一**：所有工具均通过 `ToolExecutor` 执行，不能绕过权限、超时和审计。
7. **默认离线测试**：使用 `FakeProvider` 验证核心逻辑，真实模型测试单独运行。

## 2. 推荐开发顺序

| 阶段 | 开发内容 | 阶段产物 |
|---|---|---|
| 0 | 工程初始化 | 三个包、测试框架、Lint 和类型检查可运行 |
| 1 | 核心协议 | Message、ToolCall、ToolResult、RunSpec、RuntimeEvent |
| 2 | 模型层 | `ModelProvider`、`FakeProvider`、OpenAI Compatible Adapter |
| 3 | 工具层 | Tool、Registry、Executor、PermissionPolicy |
| 4 | 最小 Agent Loop | 完成模型与工具的多轮调用闭环 |
| 5 | Session 与 Context | Tree 数据结构、线性追加、Context View |
| 6 | Coding Tools | 搜索、读取、编辑、写入、命令和 Git 工具 |
| 7 | CLI | 流式展示、审批、取消、斜杠命令和会话恢复 |
| 8 | 高级上下文 | rewind/fork、自动压缩、项目指令 |
| 9 | 动态控制 | Steering、Follow-up、Plan/Todo 和 Hooks |
| 10 | 扩展能力 | Memory、Skills、Subagents、Plugins、Extensions、MCP |

每个阶段结束时，主分支都应保持可运行、可测试。

## 3. 第一阶段：建立核心协议

优先在 `code-agent-llm` 和 `code-agent-core` 中定义稳定类型：

```text
Message
ToolCall
ModelRequest
ModelResponse
ModelEvent
ToolSpec
ToolResult
RunSpec
ExecutionContext
RuntimeEvent
SessionEntry
```

要求：

- 类型不依赖具体 Provider SDK。
- `ToolCall.arguments` 在进入 Runtime 后必须转换为已验证结构。
- `SessionEntry` 从第一版开始包含 `id`、`parent_id` 和 `schema_version`。
- `RuntimeEvent` 从第一版开始包含 `session_id`、`run_id` 和关联调用 ID。

完成标准：核心类型可以独立导入，且不存在指向 CLI 或具体 Adapter 的反向依赖。

## 4. 第二阶段：跑通最小 Agent Loop

先实现 `FakeProvider`，使用预设响应模拟模型：

```text
用户消息
  -> FakeProvider 返回 ToolCall
  -> ToolExecutor 返回 ToolResult
  -> FakeProvider 返回最终文本
  -> Agent Loop 结束
```

最小 Loop 必须支持：

- assistant 与 tool 消息正确配对；
- 未知工具和非法参数返回结构化错误；
- 无工具调用时正常结束；
- 达到轮次上限时停止；
- 单个工具失败不导致进程崩溃；
- 半截流式消息不写入 Session。

在这一阶段不要先开发 TUI、Memory 或 MCP。

## 5. 第三阶段：按顺序迭代模块

完成核心协议和 Agent Loop 后，按照开发顺序逐个实现模块。所有模块采用相同的增量方式：

1. 明确模块职责、输入输出和依赖边界。
2. 基于既有 Protocol 编写最小可运行实现。
3. 使用测试替身隔离尚未完成的依赖。
4. 覆盖正常路径、异常路径、边界条件和持久化行为。
5. 在组合根中接入模块，运行现有集成测试。
6. 在不改变调用方协议的前提下逐步补充高级能力。

模块之间只通过稳定接口协作。开发新模块时不得直接访问其他模块的内部状态，也不得为了局部功能修改 Agent Loop 的通用流程。需要增加能力时，应优先新增接口实现、策略或注册项。

每次迭代只完成一个可验证目标，并确保主分支始终可运行。若模块尚未实现，应提供 Null 实现或测试替身，而不是在调用方中增加临时分支。

## 6. 模块实现模板

开发任何新模块时，遵循以下步骤：

1. 在对应包中定义或确认稳定接口。
2. 编写最小实现或 Null 实现。
3. 编写单元测试覆盖正常、错误和边界情况。
4. 在 `ContributionSet` 或 `bootstrap` 中注册实现。
5. 使用 `FakeProvider` 增加集成测试。
6. 运行格式化、静态检查和全部测试。
7. 确认没有新增跨层反向依赖。

新增工具还需检查：

- 是否声明 `effects`、`timeout`、`concurrency_key` 和 `origin`；
- 是否统一经过 `ToolExecutor`；
- 是否可能访问工作区外路径、网络或敏感数据；
- 是否能在取消或超时后释放资源。

## 7. 测试要求

### 单元测试

每个模块至少覆盖：

- 正常路径；
- 无效输入；
- 超时和取消；
- 持久化或资源异常；
- 安全边界。

### 集成测试

每增加一项能力，补充一个 `FakeProvider` 脚本化场景。例如：

```text
读取文件 -> 修改文件 -> 运行测试 -> 最终总结
```

真实 Provider 测试不得进入默认测试套件，以避免网络、费用和随机性影响。

### 提交前检查

```text
ruff format --check
ruff check
mypy
pytest
```

提交前还应确认：

- 仓库中不存在 API Key 或真实凭据；
- 测试没有访问用户目录或工作区外文件；
- 新工具没有绕过 `ToolExecutor`；
- Session 和事件格式变更已更新 `schema_version` 或迁移逻辑。

## 8. 完成一个模块的判定标准

一个模块只有同时满足以下条件才算完成：

- 接口和职责清晰；
- 实现不违反依赖方向；
- 正常与异常路径均有测试；
- 已接入最小 Agent Loop 或组合根；
- 错误可恢复且不会泄漏敏感信息；
- 文档与实际行为一致；
- 全部已有测试继续通过。
