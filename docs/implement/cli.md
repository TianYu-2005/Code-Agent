# CLI 实现

## 目标

`code-agent-cli` 的 `cli/` 提供终端交互：自然语言任务输入、流式模型输出、工具执行状态、权限审批和 Ctrl+C 取消。CLI 只消费 `RuntimeEvent`，不向 AgentLoop 注入任何终端专用逻辑，未来可被 TUI 直接替换。

## 模块

| 模块 | 职责 |
|---|---|
| `cli/app.py` | REPL 主循环：读取输入、分发命令、运行 AgentLoop |
| `cli/renderer.py` | `RuntimeEvent` → 终端输出（流式文本、工具状态着色） |
| `cli/approval.py` | `ApprovalPort` 终端实现（y/a/n 交互）与 `ModeApprovalPort` 审批模式包装 |
| `cli/commands.py` | 斜杠命令解析（/help /new /model /permissions /tree /sessions /quit 等） |
| `cli/interrupt.py` | SIGINT → `CancellationToken` |
| `cli/sessions.py` | 工作区会话目录管理（创建/列出/恢复/导出） |
| `config.py` | TOML 配置文件 + 环境变量 + CLI 参数的四层合并加载 |
| `bootstrap.py` | 组合根：装配 Provider、Registry、Executor、Loop |

## 配置

配置来源优先级：CLI 参数 > 环境变量 > 项目配置（`<workspace>/.code-agent/config.toml`）> 全局配置（`~/.code-agent/config.toml`）> 默认值。API Key 缺失且在交互终端时自动进入首次配置向导。详见 [app-config.md](app-config.md)。

环境变量（兼容层，仍全部支持）：

```text
CODE_AGENT_API_KEY       必填（或配置文件/向导提供）
CODE_AGENT_BASE_URL      默认 https://api.deepseek.com
CODE_AGENT_MODEL         默认 deepseek-v4-flash
CODE_AGENT_MAX_TURNS     默认 100
CODE_AGENT_HOME          全局配置目录，默认 ~/.code-agent
```

`base_url` 的域名会自动加入 Provider 的可信主机列表，满足模型层的 SSRF 防护要求。

## 交互流程

```text
你> 修复 login.py 的空指针
  │
  ├─ 流式打印模型输出（MODEL_DELTA）
  ├─ ▸ 工具执行中... [success/error/denied]
  ├─ 需要审批时：
  │    工具: write_file
  │    目标: /path/login.py (write)
  │    允许执行？[y]允许 [a]本会话允许 [n]拒绝:
  ├─ Ctrl+C → 取消当前模型请求或工具执行
  └─ 结束后回到输入提示
```

## 事件驱动渲染

`TerminalRenderer` 是纯事件消费者：

- `MODEL_DELTA` → 逐字流式输出
- `TOOL_STARTED` / `TOOL_COMPLETED` → 带颜色的状态行
- 不直接访问 Session、Provider 或工具

这保证了按设计文档的承诺：换成 TUI 时只需提供新的 Renderer 和 ApprovalPort，核心层零改动。

## 行编辑

输入行由 `prompt_toolkit` 驱动（`PromptSession.prompt_async`）：

- 宽字符（中文）与 ANSI 样式的列宽计算正确，退格/光标移动不卡壳；
- 内置历史记录（↑/↓ 翻阅）与标准行编辑快捷键；
- `MODEL_STARTED` 事件渲染为换行，回车提交后立即有视觉反馈，首个流式 token 到达前不再静默。

此前裸 `input()` 依赖终端 readline：提示符中的 ANSI 转义与宽字符都会被误算列宽，导致退格删除错位、行编辑状态混乱。

## 会话持久化

会话自动持久化到 `<workspace>/.code-agent/sessions/<session-id>.jsonl`（`SessionFileStore` 逐条追加），首次创建目录时写入 `.code-agent/.gitignore` 忽略会话数据。

`SessionManager`（`cli/sessions.py`）负责目录级管理：

- `create()`：新建带时间戳 ID 的持久化会话（CLI 启动与 `/new` 都走这里）
- `list_sessions()`：扫描目录、跳过空会话，按更新时间倒序返回摘要（标题取首条用户消息）
- `load(id)` / `export_markdown(id)`：恢复会话、导出 Markdown 纪录

`/sessions` 命令：无参数列出；`/sessions <序号>` 恢复（自动清理未使用的空会话文件）；`/sessions export <序号> [路径]` 导出。会话标题自动从首条消息生成，暂不提供重命名。恢复或新建会话后，组合根通过 `_rebind_session()` 重建 ContextManager 与 RunSpec（session_id 来自文件名）。

## rewind / fork

树状会话的终端入口：

- `/rewind`：显示当前分支最近 8 条消息（`-n` 距今步数）；`/rewind <n>` 把对话头回退 n 条，后续对话从该点分叉，原消息保留在树中
- `/fork`：列出分支（含当前标记）；`/fork <序号>` 切换到指定分支继续
- `/tree`：显示分支头与消息数（标记当前分支）

当前位置通过 `.head` sidecar 文件持久化：`SessionFileStore` 在每次 append / rewind / fork 后写入当前 head，重启恢复时若 `.head` 有效则优先采用——回退或分叉后重启不会丢失位置。本次同时修复了 `list_branches` 的存量缺陷：分支消息数此前只统计 head 自身，现改为从根到 head 的完整路径长度。

## 组合根

`bootstrap.AgentRuntime` 是唯一允许同时依赖抽象和具体实现的组装点：

- 创建 `RetryingProvider(OpenAICompatibleProvider)`
- 注册全部 12 个内置 Coding Tools（含后台进程生命周期工具）
- 加载 `AGENTS.md` 项目指令进 ContextPolicy
- 用 `ModeApprovalPort` 包装 `TerminalApprovalPort`（支持运行时 ask/auto 切换）
- 绑定 `CancelState`；提供 `switch_model()` 运行时切换模型/profile

入口命令：`code-agent`（pyproject 的 project.scripts，`--cli` 走经典 REPL，默认 TUI）。

## 测试

- 命令解析：聊天 / 命令 / 退出三分类
- 配置加载：缺失 API Key 报错、环境变量读取、可信域名推导
- 渲染器：流式文本、工具状态、banner
- 审批端口：y 确认、输入流可注入
- 取消状态：信号状态转换
- 组合根冒烟：12 个工具装配、Loop 创建成功
- 会话持久化：创建（含 .gitignore）、列出/恢复/导出往返、空会话清理、非法 ID 拒绝、组合根绑定与切换会话
