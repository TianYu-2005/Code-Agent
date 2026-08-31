# Session 与 Context 实现

## 目标

- Session 保存真实发生的对话与运行事实，采用树状结构，支持回退和分支。
- Context 从 Session 当前分支构造模型输入，按 Token 预算裁剪历史，不修改 Session。

## Session

### 存储

`session_store.py` 提供 `SessionStore`（内存）与 `SessionFileStore`（JSONL 持久化）。

数据结构沿用 `session.py` 协议：每个 `SessionEntry` 带 `id` 和 `parent_id`，线性使用时退化为链表。

### 功能

| 能力 | 说明 |
|---|---|
| `append` | 校验父节点存在、ID 唯一后追加；只有 MESSAGE 推进当前对话头 |
| `append_event` | 记录运行事件，不进入对话路径 |
| `current_path` | 返回根到当前头的消息序列 |
| `messages` | 当前分支的消息 payload |
| `rewind` | 回退到当前分支上的历史消息 |
| `fork` | 从任意消息节点开始新分支 |
| `list_branches` | 列出各分支头及消息数 |

### 持久化

`SessionFileStore` 追加写 JSONL；恢复时逐行 `model_validate_json` 解析，遇到损坏行截断尾部并重写文件。恢复后可继续追加新对话。

## Context

`context.py` 的 `ContextManager` 按以下顺序构建 `ModelRequest`：

1. System Prompt（默认或自定义）
2. 项目指令（读取工作区 `AGENTS.md`）
3. Session 当前分支消息 + 本次额外输入

裁剪策略：按“字符数 / 4”估算 Token，超预算时从最旧消息开始丢弃，保留最近完整对话。Context 只读 Session，追加的额外消息也不写回。

`describe_context` 返回包含消息数、估算 Token 和是否截断的调试视图。

## 测试

- 线性追加、当前路径与消息读取
- 运行事件不推进对话头
- rewind / fork / 分支列表
- 重复 ID、未知父节点、重复根节点拒绝
- 文件往返、恢复后继续对话、损坏尾部截断
- Context 构建、AGENTS.md 注入、额外消息不修改 Session
- 预算裁剪、工具调用与结果配对、Token 估算
