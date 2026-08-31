# Code Agent 设计文档

> 状态：Draft v2

## 1. 项目目标

本项目实现一个可扩展的终端 Coding Agent。Agent 通过大语言模型理解任务，自主搜索、读取和修改代码，执行命令验证结果，并在多轮工具调用后给出结论。

项目自行实现 Agent Loop、模型协议适配、工具执行、会话与上下文管理、错误处理及终止机制，不依赖 Agent 框架或服务端托管的文件、代码执行能力。

设计采用“**最终架构先行、功能渐进实现**”原则：先固定模块协议、依赖方向和数据模型，MVP 使用最小实现，后续通过增加实现与注册项扩展能力，不重写 Agent 核心。

## 2. 最终版本功能

### 2.1 编程能力

| 功能 | 说明 |
|---|---|
| 项目探索 | 按路径和 Glob 查找文件，按正则搜索内容，分页读取文本文件 |
| 文件修改 | 精确替换、创建文件、应用补丁；支持原子写入和并发保护 |
| 命令执行 | 执行构建、测试和格式化命令，支持超时、取消和输出截断 |
| Git 辅助 | 查看状态和差异，生成改动摘要；不默认提交或推送 |
| 任务验证 | 修改后主动运行相关测试或检查，并记录验证结果 |
| 结果总结 | 汇总修改文件、关键决策、验证结果和遗留问题 |

### 2.2 Agent 运行能力

| 功能 | 说明 |
|---|---|
| ReAct Loop | 在“模型推理—工具执行—结果反馈”之间循环，直至完成或终止 |
| 流式响应 | 实时展示模型文本、工具调用状态和执行结果 |
| Steering | 任务运行期间接收纠偏消息，在安全点注入当前任务 |
| Follow-up | 当前任务完成后自动处理排队的后续任务 |
| 取消与限制 | 支持取消、轮次上限、总时限、Token 预算和重复调用检测 |
| Plan/Todo | 维护复杂任务的计划、步骤状态和完成情况 |
| 生命周期事件 | 暴露运行、模型、工具、上下文和子任务事件 |
| Hooks | 在输入、上下文、模型调用和工具执行阶段提供受控扩展点 |

### 2.3 模型能力

- 统一 OpenAI Compatible、Anthropic 等 Provider 的消息、工具调用、流式事件和用量数据。
- 支持运行时选择 Provider、模型、上下文窗口和推理参数。
- 支持超时、取消、限次重试和错误归一化。
- 使用模型能力描述处理 Tool Calling、流式调用和上下文长度差异。
- API Key 仅从环境变量读取，日志默认脱敏。

### 2.4 会话与上下文

| 功能 | 说明 |
|---|---|
| 持久化会话 | 保存用户消息、模型回复、工具调用、工具结果和运行事件 |
| 树状历史 | 支持回退到历史节点并创建新分支，保留所有分支 |
| 会话恢复 | 列出、恢复、重命名和导出历史会话 |
| 上下文构建 | 从当前会话分支生成本轮模型输入，不修改原始历史 |
| 自动压缩 | 接近上下文上限时摘要旧消息，并保留目标、改动和未完成事项 |
| 项目指令 | 加载工作区中的 `AGENTS.md` 等项目级约束 |
| 长期记忆 | 保存经确认的项目事实、用户偏好和可复用经验 |

### 2.5 扩展能力

| 功能 | 说明 |
|---|---|
| Skills | 发现并按需加载可复用的任务说明和工作流 |
| Subagents | 将子任务委派给拥有独立上下文、预算和工具集的 Agent |
| Plugins | 将 Skills、Subagent 定义、Extensions 和 MCP 配置组织为分发单元 |
| Extensions | 注册工具、命令、Hook 和 Prompt Contributor |
| MCP | 连接受信任的 MCP Server，并将其能力统一注册为工具 |
| 多入口 | 复用同一运行时支持交互式 CLI、非交互 JSON 和 Python API |

### 2.6 终端交互

- 自然语言多轮对话和流式展示。
- `@path` 引用项目文件。
- `/new`、`/sessions`、`/tree`、`/compact`、`/model`、`/skills`、`/agents`、`/details`、`/help` 和 `/quit`。
- 工具执行摘要、可展开详情和权限审批。
- `Ctrl+C` 取消当前模型请求或工具执行。
- 运行中输入作为 Steering，排队输入作为 Follow-up。

### 2.7 安全能力

- 工具执行采用 `allow / ask / deny` 三态权限策略。
- 普通工作区读取默认允许，文件修改和命令执行默认询问。
- 敏感文件及工作区外访问默认拒绝。
- 所有文件路径经过规范化、真实路径和符号链接检查。
- 命令默认使用参数数组和 `shell=False`，限制工作目录、环境变量和执行时间。
- 本地工具、Subagent、Extension 和 MCP 工具均经过同一个安全执行入口。
- 记录权限决策和工具影响范围，支持审计。

## 3. 总体架构

系统分为模型层、Agent 核心层和 Coding Agent 产品层，依赖关系保持单向：

```text
┌──────────────────────────────────────────────────────────┐
│ code-agent-cli                                           │
│ CLI / Commands / Renderer / Approval / Bootstrap         │
│ Coding Tools / MCP / Extensions                          │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│ code-agent-core                                          │
│ Agent Runtime / Tool System / Session / Context           │
│ Hooks / Memory / Skills / Subagents / Plugins             │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────┐
│ code-agent-llm                                           │
│ Unified Types / Provider Protocol / Provider Adapters     │
└──────────────────────────────────────────────────────────┘
```

三个包的职责如下：

| 包 | 职责 | 不负责 |
|---|---|---|
| `code-agent-llm` | 模型协议、统一类型、Provider 适配 | Agent Loop、工具和会话 |
| `code-agent-core` | Agent 编排及通用扩展机制 | 终端 UI 和具体编程工具 |
| `code-agent-cli` | 产品装配、编程工具、权限交互和 CLI | 模型协议与通用运行逻辑 |

## 4. 工程结构

```text
Code-Agent/
├── pyproject.toml
├── packages/
│   ├── code-agent-llm/
│   │   ├── src/code_agent_llm/
│   │   │   ├── types.py
│   │   │   ├── provider.py
│   │   │   ├── registry.py
│   │   │   └── providers/
│   │   └── tests/
│   ├── code-agent-core/
│   │   ├── src/code_agent_core/
│   │   │   ├── runtime/
│   │   │   ├── tools/
│   │   │   ├── session/
│   │   │   ├── context/
│   │   │   ├── hooks/
│   │   │   ├── memory/
│   │   │   ├── skills/
│   │   │   ├── subagents/
│   │   │   └── plugins/
│   │   └── tests/
│   └── code-agent-cli/
│       ├── src/code_agent/
│       │   ├── cli/
│       │   ├── coding_tools/
│       │   ├── permissions/
│       │   ├── extensions/
│       │   ├── mcp/
│       │   ├── config.py
│       │   └── bootstrap.py
│       └── tests/
└── docs/
    └── design.md
```

每个包使用 `src` 布局。根项目统一管理依赖、格式化、静态检查和测试。

## 5. 核心数据协议

以下协议在 MVP 阶段确定并保持稳定。

### 5.1 模型协议

```text
Message
- id
- role: system | user | assistant | tool
- content
- tool_calls
- tool_call_id
- metadata

ToolCall
- id
- name
- arguments

ModelRequest
- messages
- tools
- model
- parameters

ModelResponse
- content
- tool_calls
- finish_reason
- usage

ModelEvent
- text_delta | tool_call_delta | usage | completed
```

`ModelProvider` 只负责模型通信：

```text
stream(ModelRequest, CancellationToken) -> AsyncIterator[ModelEvent]
```

Provider 不访问 Session，不执行工具，也不依赖 CLI。

### 5.2 工具协议

```text
ToolSpec
- name
- description
- input_schema
- effects
- timeout
- concurrency_key
- origin

ToolResult
- status: success | error | denied | timeout | cancelled
- content
- metadata

Tool
- spec
- execute(arguments, ExecutionContext) -> ToolResult
```

`effects` 描述工具可能产生的影响，例如 `read`、`write`、`execute`、`network`。`origin` 标记工具来自内置模块、Extension、MCP 或 Plugin。

### 5.3 运行协议

```text
RunSpec
- session_id
- parent_run_id
- agent_name
- model
- tool_set
- budgets
- metadata

ExecutionContext
- workspace
- session_id
- run_id
- cancellation
- permission_context
- event_sink
```

使用不可变 `RunSpec` 描述一次运行，避免随着功能增加不断扩张 `Agent` 构造函数。

### 5.4 事件协议

每个事件统一包含：

```text
schema_version, timestamp, session_id, run_id,
task_id, turn_id, model_call_id, tool_call_id, parent_run_id
```

主要事件：

- `RunStarted`、`RunCompleted`、`RunFailed`；
- `TurnStarted`、`TurnCompleted`；
- `ModelStarted`、`ModelDelta`、`ModelCompleted`；
- `ToolRequested`、`PermissionRequested`、`ToolStarted`、`ToolCompleted`；
- `ContextCompacted`、`SteeringReceived`、`FollowUpQueued`；
- `Cancelled`、`LimitReached`。

开始事件必须存在对应终态事件，包括拒绝、超时和取消。

## 6. Agent Runtime

### 6.1 组件职责

| 组件 | 职责 |
|---|---|
| `AgentService` | 创建或恢复 Agent 运行，协调外部用例 |
| `LoopController` | 执行双层 Agent Loop 和状态迁移 |
| `RunSupervisor` | 管理取消、预算、子任务和运行句柄 |
| `InterventionQueue` | 管理 Steering 与 Follow-up |
| `ToolExecutor` | 校验、授权、执行、超时、截断和审计工具调用 |
| `HookPipeline` | 执行观察型和变换型 Hook |
| `EventBus` | 发布统一运行事件 |

`LoopController` 只依赖稳定协议，不直接访问 CLI、模型 SDK、文件系统或 MCP。

### 6.2 双层循环

外层循环处理 Follow-up，内层循环处理当前任务中的模型和工具迭代：

```text
接收用户任务
  │
  ▼
Input Hook -> 写入当前 Session 分支
  │
  ▼
外层 Task Loop
  ├─ 开始当前任务
  │   │
  │   ▼
  │  内层 ReAct Loop
  │   ├─ 检查取消和预算
  │   ├─ 注入待处理 Steering
  │   ├─ 构建 ContextView
  │   ├─ 调用模型并暂存流式结果
  │   ├─ 原子提交完整 assistant 消息
  │   ├─ 无工具调用：结束当前任务
  │   └─ 有工具调用：执行并写入结果，继续循环
  │
  ├─ 有 Follow-up：开始下一任务
  └─ 无 Follow-up：完成运行
```

处理优先级固定为：

```text
Cancel > 硬限制 > 安全策略 > Steering > 正常工具续跑 > Follow-up
```

### 6.3 单轮执行规则

1. 检查取消、最大轮次、总时限、Token 预算和重复调用。
2. 将待处理 Steering 写入当前会话分支。
3. `ContextManager` 创建不可变 `ContextView`。
4. 执行模型调用前 Hook。
5. 流式数据先进入 staging buffer，不把半截消息写入会话。
6. 聚合并校验完整 `ModelResponse`。
7. 原子提交 assistant 消息。
8. 对每个 Tool Call 执行 Schema 校验、Hook、二次校验和权限判定。
9. `ToolExecutor` 执行工具；每个 Tool Call 必须产生一个 Tool Result。
10. 工具结果按调用顺序写入会话，然后进入下一轮。

默认限制由配置提供，运行时不写死具体值。

## 7. 工具系统

### 7.1 注册与执行

`ToolRegistry` 负责工具注册、冲突检查和 Schema 导出。所有工具调用必须经过 `ToolExecutor`：

```text
ToolCall
  -> 查找 Tool
  -> 参数校验
  -> BeforeTool Hook
  -> 二次校验
  -> PermissionPolicy
  -> 超时与取消包装
  -> Tool.execute
  -> 输出截断与脱敏
  -> AfterTool Hook
  -> ToolResult
```

工具自身不能弹出审批界面或写入 Session。审批通过 `ApprovalPort` 完成，会话写入由 Runtime 负责。

### 7.2 内置 Coding Tools

| 工具 | 作用 |
|---|---|
| `list_files` | 按目录或 Glob 列出文件 |
| `search_text` | 按文本或正则搜索代码 |
| `read_file` | 分页读取文本文件 |
| `replace_in_file` | 唯一精确匹配后替换文本 |
| `write_file` | 创建文件或经审批覆盖文件 |
| `apply_patch` | 应用结构化补丁 |
| `run_command` | 执行构建、测试和格式化命令 |
| `git_status` | 获取工作区状态 |
| `git_diff` | 获取代码差异 |
| `todo` | 维护任务计划 |
| `memory` | 查询或写入长期记忆 |
| `task` | 创建 Subagent 子任务 |

### 7.3 并发模型

- MVP 中所有工具串行执行。
- 最终版允许只读且无冲突的工具并行。
- `effects` 决定工具是否只读，`concurrency_key` 标识共享资源。
- 文件写入使用规范化路径作为锁键。
- 工具结果始终按原始 Tool Call 顺序写回模型。

## 8. 权限与本地执行

### 8.1 权限模型

权限决策为 `allow`、`ask` 或 `deny`。规则可按工具、影响类型、路径和命令模式配置，具体规则优先于通用规则。

默认策略：

| 操作 | 策略 |
|---|---|
| 搜索及读取普通工作区文件 | `allow` |
| 修改、新建或覆盖文件 | `ask` |
| 只读 Git 操作 | `allow` |
| 普通命令 | `ask` |
| 敏感文件、提权和破坏性命令 | `deny` |
| 工作区外路径 | `deny` |
| 未声明的网络访问 | `ask` 或 `deny` |

审批支持仅本次允许、当前会话允许和拒绝。安全策略在 `ToolExecutor` 最终执行点强制应用，Hook、Extension、MCP 和 Subagent 不得绕过。

### 8.2 文件安全

`WorkspacePathResolver` 统一完成：

1. 路径规范化；
2. `..` 和绝对路径处理；
3. 符号链接解析；
4. 工作区边界检查；
5. 敏感文件检查；
6. 执行前二次确认。

文件修改使用共享锁和“临时文件 + `fsync` + 原子替换”。精确替换要求旧文本恰好出现一次，否则不修改。

### 8.3 命令安全

- 默认接收 `argv: list[str]` 并使用 `shell=False`。
- `cwd` 必须位于授权工作区。
- 子进程仅继承 allowlist 中的环境变量，不继承模型 API Key。
- 使用独立进程组；取消或超时时清理整个进程组。
- 限制执行时间和输出大小。
- 拒绝提权、交互式命令和已知破坏性操作。

## 9. Session 与 Context

### 9.1 Tree Session

Session 是完整事实记录，从 MVP 开始即使用树状数据模型：

```text
SessionEntry
- id
- parent_id
- type
- timestamp
- payload
- schema_version
```

MVP 仅沿当前分支追加，最终版增加：

- `rewind(entry_id)`：把当前指针移动到历史节点；
- `fork(entry_id)`：从指定节点创建分支；
- `current_path()`：返回根到当前节点的消息路径；
- `list_branches()`：列出分支；
- `export()`：导出当前分支或整棵树。

存储采用 append-only journal，并定期生成 checkpoint。写入必须原子化，尾部损坏时可恢复到最后一个合法事件。

### 9.2 Context View

Session 与 Context 严格分离：

- Session 保存真实历史；
- Context 只构造本轮模型看到的临时视图；
- 裁剪、摘要和 Hook 修改不能覆盖原始消息。

`ContextManager` 按以下顺序构建上下文：

1. 稳定 System Prompt；
2. 项目指令；
3. Skills 清单；
4. 已确认的 Memory；
5. 当前分支的压缩摘要；
6. 最近完整消息；
7. 当前任务和 Steering。

MVP 使用 Identity/Sliding Window Policy；最终版增加自动压缩。压缩产物必须记录所属分支、覆盖的消息范围、摘要模型和内容哈希，避免跨分支误用。

## 10. Hooks 与扩展机制

### 10.1 Hook 类型

Hooks 分为两类：

- Observer：只观察事件，用于日志、指标和 UI。
- Transform Hook：返回有类型的决策，可修改特定阶段输入。

主要扩展点：

- `on_user_input`；
- `before_context_build` / `after_context_build`；
- `before_model_call` / `after_model_call`；
- `before_tool_call` / `after_tool_call`；
- `on_run_start` / `on_run_end`。

Hook 具有优先级、超时和明确的失败策略。权限检查不是普通 Hook，始终在最终执行前裁决。

### 10.2 统一贡献模型

内置模块和外部扩展统一产生 `ContributionSet`：

```text
ContributionSet
- tools
- commands
- hooks
- prompt_contributors
- skill_sources
- subagent_definitions
- mcp_server_definitions
```

MVP 由内置代码静态生成贡献；后续 Plugin 和 Extension 只增加贡献来源，不修改 Agent 构造和运行流程。

## 11. Memory、Skills 与 Subagents

### 11.1 Memory

Memory 与 Session 分离：Session 记录发生过什么，Memory 保存跨会话仍有价值的事实。

- `MemoryRepository` 提供查询、写入、删除和更新接口。
- Memory 记录来源、作用域、确认状态和更新时间。
- Agent 写入 Memory 需经过权限策略；未确认内容不能进入高优先级 Prompt。
- `PromptContributor` 只注入与当前任务相关的记忆摘要。

### 11.2 Skills

Skill 是包含名称、描述和正文的声明式能力包：

- 启动时仅把 Skill 清单加入 Prompt；
- 模型按需加载正文，减少上下文占用；
- 支持用户级、项目级和 Plugin 级来源；
- Skill 本身不直接获得执行权限，仍需通过已注册工具完成操作。

### 11.3 Subagents

`task` 工具通过 `TaskSupervisor` 和 `AgentFactory` 创建子 Agent：

- 子 Agent 拥有独立 Session、Context 和预算；
- 只获得显式允许的工具子集；
- 父 Agent 接收结构化任务结果，不直接合并完整子会话；
- 取消信号向子任务传播；
- 支持限制深度、并发数和总预算；
- 子 Agent 的所有工具调用仍经过统一权限系统。

## 12. Plugins、Extensions 与 MCP

### 12.1 Plugins

Plugin 是资源分发单元，通过 manifest 声明包含的 Skills、Subagent、Extension 和 MCP 配置。Plugin 默认只提供声明式资源，不因被发现而自动执行代码。

### 12.2 Extensions

Extension 是受信任的可执行扩展：

- 必须显式启用；
- 通过能力受限的 `ExtensionRegistrar` 注册贡献；
- 不能获得整个 Agent 实例；
- 不能覆盖内置安全组件和同名核心工具；
- 加载失败不影响核心 Agent 启动。

### 12.3 MCP

MCP Client 负责连接、能力发现和协议转换：

```text
MCP Server -> MCPToolProvider -> ToolRegistry -> ToolExecutor
```

MCP 工具必须：

- 使用命名空间避免冲突；
- 标记来源、影响类型和并发属性；
- 经过统一参数校验、权限、超时、取消和审计；
- 使用环境变量 allowlist，不继承完整进程环境；
- 仅启动用户明确授权的命令或连接明确授权的地址。

## 13. 配置与装配

`bootstrap` 是唯一组合根，负责创建具体 Provider、Repository、Tools、Policies、Extensions 和 CLI Adapter。

配置优先级：

```text
CLI 参数 > 环境变量 > 项目配置 > 用户配置 > 默认值
```

密钥只允许来自环境变量。配置文件保存模型名、Provider 地址、权限、预算、扩展和 MCP 声明，不保存凭据。

## 14. 可扩展性约束

为保证从 MVP 演进到最终版时不推翻架构，必须遵守：

1. MVP 虽只使用线性历史，存储结构仍是 Tree Session。
2. MVP 虽不做摘要，所有模型输入仍经过 `ContextManager`。
3. MVP 虽只有一个 Provider，Runtime 仍只依赖 `ModelProvider`。
4. MVP 虽没有 Plugin，内置能力仍通过 `ContributionSet` 注册。
5. MVP 虽串行执行工具，`ToolSpec` 仍包含 `effects` 和 `concurrency_key`。
6. MVP 虽没有 Subagent，Runtime 从一开始使用 `RunSpec` 和 `RunSupervisor`。
7. 所有事件和持久化记录从第一版携带 `schema_version`。
8. 具体实现只能依赖核心协议，不能被 Runtime 反向引用。
9. 安全策略集中在 `ToolExecutor`，任何新增能力不得建立旁路。

## 15. 分阶段开发计划

| 阶段 | 目标 | 功能 |
|---|---|---|
| 0. 架构基础 | 固定稳定接口 | 核心数据类型、Protocol、事件、`RunSpec`、`ContributionSet`、Null 实现 |
| 1. MVP 闭环 | 完成真实编程任务 | 单 Provider、ReAct Loop、Tree Session 线性使用、工具注册、读搜改写、命令、权限、Fake Provider 测试 |
| 2. 可靠运行 | 提高可用性 | 流式输出、取消、重试、超时、进程清理、原子写、会话恢复、审计 |
| 3. 会话与上下文 | 支持长任务 | rewind/fork、自动压缩、工具大输出落盘、项目指令、会话导出 |
| 4. 动态交互 | 支持运行中控制 | Steering、Follow-up、Plan/Todo、完整斜杠命令 |
| 5. 知识与协作 | 增强任务能力 | Skills、Memory、Subagents、预算和能力隔离 |
| 6. 扩展生态 | 支持外部能力 | 多 Provider、Plugins、Extensions、MCP、JSON API |
| 7. 最终强化 | 完善效率与隔离 | 只读工具并行、模型能力表、可选沙箱、指标与迁移工具 |

每一阶段都应形成可运行版本，并在现有测试基础上增量开发。

## 16. 测试设计

### 16.1 单元测试

- Provider 协议转换和流式聚合；
- Agent 状态转换、消息配对和终止条件；
- Tool Schema、错误包装、超时和输出截断；
- 权限规则和工作区路径边界；
- Tree Session 的追加、回退、分支和恢复；
- Context 构建及压缩分支绑定；
- Steering、Follow-up 和取消优先级；
- Memory、Skills、Subagent 和 Extension 权限边界。

### 16.2 集成测试

使用脚本化 `FakeProvider` 离线验证：

1. 搜索、读取、编辑和测试的完整闭环；
2. 工具失败后模型修正参数；
3. 权限拒绝后的安全恢复；
4. 中途 Steering 和任务结束后的 Follow-up；
5. 会话恢复、回退和分支；
6. 上下文压缩后继续执行；
7. 父子 Agent 的任务委派和取消；
8. MCP 工具仍经过统一权限入口。

### 16.3 工程检查

- `ruff format --check`；
- `ruff check`；
- `mypy`；
- `pytest`；
- 核心模块分支覆盖率不低于 80%。

## 17. 验收标准

最终版本应满足：

- 能自主完成“理解项目—定位代码—修改实现—运行验证—总结结果”的任务；
- 多 Provider 可替换而不修改 Agent Runtime；
- 支持树状会话、自动压缩、Steering 和 Follow-up；
- Skills、Memory、Subagents、Extensions 和 MCP 均通过统一协议接入；
- 所有工具调用可观察、可取消、可审计，并经过统一权限控制；
- 核心测试无需真实网络和模型即可稳定运行；
- 新增 Provider、Tool、Skill 或入口时无需修改 Agent Loop。
