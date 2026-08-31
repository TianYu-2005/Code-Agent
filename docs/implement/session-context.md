# Session 与 Context 实现

## 目标

- Session 保存真实发生的对话与运行事实，采用树状结构，支持回退和分支。
- Context 从 Session 当前分支构造模型输入，按 Token 预算裁剪历史，不修改 Session。
- 长对话接近预算时自动压缩：旧消息摘要成一条 SYSTEM 消息，保留最近完整轮次。

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
| `list_branches` | 列出各分支头及消息数（从根到 head 的完整路径长度） |
| `latest_compaction` | 返回前缀匹配当前分支的最新压缩（payload + 覆盖条数） |

### 持久化

`SessionFileStore` 追加写 JSONL；恢复时逐行 `model_validate_json` 解析，遇到损坏行截断尾部并重写文件。恢复后可继续追加新对话。当前对话头位置写入 `<session>.head` sidecar 文件（append / rewind / fork 后更新），恢复时优先采用——回退或分叉后重启不丢失位置。

## Context

`context.py` 的 `ContextManager` 按以下顺序构建 `ModelRequest`：

1. System Prompt（默认或自定义）
2. 项目指令（读取工作区 `AGENTS.md`）
3. 当前分支的压缩摘要（存在有效压缩时，SYSTEM 消息）
4. 摘要未覆盖的分支消息 + 本次额外输入

裁剪策略：按“字符数 / 4”估算 Token，超预算时从最旧消息开始丢弃，保留最近对话，并把窗口起点对齐到 user 消息——不允许拆散 assistant(tool_calls) 与 tool 结果的配对。Context 只读 Session，追加的额外消息也不写回。

`describe_context` 返回包含消息数、估算 Token 和是否截断的调试视图。

## 自动压缩

`compaction.py` 的 `Compactor` 由 AgentLoop 在每轮构建请求前调用：

- **触发**：当前分支估算 Token > `token_budget × 0.8`（`CompactionPolicy.trigger_ratio`）
- **范围**：保留最近 2 个完整轮次（`keep_recent_turns`，对齐 user 边界），之前的消息进入摘要
- **摘要**：用同一 Provider 发一次无工具请求，提示词固定要求保留任务目标、已完成改动、关键决策与文件路径、未完成事项
- **产物**：`COMPACTION` entry 追加进树（parent 挂在被压缩段末尾，不推进对话头）；payload 含 `summary`、`source_entry_ids`（完整覆盖范围）、`branch_head_id`、`model`、`content_hash`（摘要 SHA-256）
- **分支绑定**：`SessionStore.latest_compaction()` 只接受“source 范围恰为当前路径前缀”的压缩——rewind 到压缩范围内分叉后旧摘要自动失效，回退到更早位置则使用原文
- **增量**：再次触发时，上一次摘要 + 新旧消息一起作为摘要输入，`source_entry_ids` 扩展为完整范围，摘要不套娃
- **降级**：摘要请求失败或结果为空时返回 `failed`，本轮退化为 trim 截断，任务不中断

压缩完成（或失败）通过 `CONTEXT_COMPACTED` 事件上报（带 `turn_id`），CLI 渲染为 `◇ 上下文已自动压缩（N 条历史消息转为摘要）`。

## 测试

- 线性追加、当前路径与消息读取
- 运行事件不推进对话头
- rewind / fork / 分支列表（含消息数）
- 重复 ID、未知父节点、重复根节点拒绝
- 文件往返、恢复后继续对话、损坏尾部截断、head 位置持久化
- Context 构建、AGENTS.md 注入、额外消息不修改 Session
- 预算裁剪、工具调用与结果配对、Token 估算、裁剪后 user 边界对齐
- 压缩：阈值以下跳过、摘要写入与请求注入、保留窗口不拆散工具配对、增量压缩包含旧摘要、失败降级为 trim、Loop 集成事件与最终请求内容
