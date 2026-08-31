# Agent Loop 实现

## 目标

`runtime/loop.py` 的 `AgentLoop` 把模型层、工具层、Session 和 Context 连接成完整闭环：用户输入 → 模型推理 → 工具执行 → 结果反馈 → 再次推理，直到给出最终回答或达到限制。

## 循环流程

```text
run(user_message)
  │
  ├─ 写入用户消息到 Session
  ├─ RUN_STARTED
  │
  ▼ 最多 max_turns 轮
  TURN_STARTED
  ├─ ContextManager 构建请求（含工具列表）
  ├─ MODEL_STARTED
  ├─ 流式消费模型事件，转发 TEXT_DELTA
  ├─ MODEL_COMPLETED
  ├─ 写入 assistant 消息
  │
  ├─ 无 tool_calls → TURN_COMPLETED → COMPLETED
  │
  └─ 有 tool_calls → 逐个执行：
       TOOL_STARTED
       ├─ ToolExecutor 验证、授权、执行
       ├─ 写入 tool 结果消息
       └─ TOOL_COMPLETED
       → 回到 TURN_STARTED
  │
  ▼
  RUN_COMPLETED
```

## 组件协作

| 组件 | Loop 中的职责 |
|---|---|
| `ModelProvider` | 流式获取模型响应 |
| `ContextManager` | 每轮从 Session 构建请求，注入工具 Schema |
| `ToolRegistry` | 导出 `RunSpec.tool_set` 对应的工具规格 |
| `ToolExecutor` | 验证参数、权限判定、执行、超时和取消 |
| `SessionStore` | 持久化 user / assistant / tool 消息 |
| `EventSink` | 发布带关联 ID 的生命周期事件 |

Loop 自身不做参数校验、权限判断或上下文裁剪，全部委托给对应模块，保持编排层简单。

## 终止条件

| 原因 | 触发 |
|---|---|
| `COMPLETED` | 模型返回无 tool_calls 的最终回答 |
| `MAX_TURNS` | 达到 `RunBudgets.max_turns` |
| `CANCELLED` | CancellationToken 触发或模型请求被取消 |
| `ERROR` | Provider 错误或流式响应异常 |

每个 ToolCall 保证有配对的 tool 消息写入 Session，即使工具被拒绝或执行失败。

## 测试

使用 `FakeProvider` 脚本化模型响应，完全离线验证：

- 直接给出最终回答时单轮完成
- 请求工具 → 执行 → 二轮给出回答的完整闭环
- Session 中消息顺序和 `tool_call_id` 配对正确
- 达到 `max_turns` 时停止并报告
- Provider 错误归类为 `ERROR`
- 生命周期事件按序发出（run/turn/model/tool 的 started 和 completed）
