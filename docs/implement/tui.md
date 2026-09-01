# TUI 实现

## 目标

以 Textual 实现 inline 风格的终端 UI 作为默认入口：滚动对话流、流式输出、工具状态、审批面板和状态栏。TUI 只消费 `RuntimeEvent` 并通过 `ApprovalPort` 交互，与 CLI 共享同一组合根，核心层零改动。

## 技术选型

- **Textual 8.x**：Python 生态唯一成熟的组件化 TUI 框架（组件、CSS、异步、消息驱动）
- **Inline 模式**（`App.run(inline=True)`）：与 Claude Code / Codex CLI 同款形态——不切换备用屏幕，固定高度区域渲染在终端底部，历史在应用内滚动
- 主流对照：Claude Code/Gemini CLI 用 Ink（React），OpenCode 用 Bubble Tea（Go）；Python 对应即 Textual

## 模块

| 模块 | 职责 |
|---|---|
| `tui/app.py` | `CodeAgentApp`：inline 布局、事件渲染、命令处理、worker 调度 |
| `tui/renderer.py` | `TuiRenderer`（EventSink → Textual 消息）、`TuiApprovalPort`（审批 → 键位应答） |
| `tui/messages.py` | `AgentEvent` / `ApprovalAsked` / `TaskFinished` 消息类型 |
| `tui/styles.tcss` | 布局与配色（圆角边框、accent 高亮、审批警示色） |

## 布局

```text
┌ transcript ────────────────┐  滚动对话流（RichLog）
│ ──────────────             │  轮次分隔线（dim）
│ ❯ 用户消息                  │  加粗青色前缀
│ Agent                      │  助手标签 + Markdown 正文
│   ⏺ write_file(path=a) ✓  │  工具单行摘要（参数压缩）
├────────────────────────────┤
│ streaming（流式中的回复）    │  左侧 accent 竖线，完成后落入 transcript
├────────────────────────────┤
│ approval（审批面板，条件显示）│  黄色警示，y/a/n 键位
├────────────────────────────┤
│ > 输入框（增高）             │  圆角边框，聚焦高亮，placeholder 含快捷键提示
│ ● 状态 · 模型 · 模式 · 目录  │  状态栏（审批模式实时反映）
└────────────────────────────┘
```

## 视觉节奏

对标 Claude Code：用户消息上方细线分隔成块；工具调用压缩为单行 `⏺ 工具名(key=value…) 状态符`——参数最多取 2 个关键键（path/command/pattern 等优先，值截断 48 字符），完整 JSON 不落屏；工具行之间零空行，轮次间由分隔线承担呼吸感。`/model`、`/permissions` 切换后状态栏即时刷新。

## 事件流

```text
AgentLoop ──RuntimeEvent──▶ TuiRenderer.emit()
                                │ post_message(AgentEvent)
                                ▼
                          CodeAgentApp.on_agent_event()
                                │ 按 MODEL_DELTA / MODEL_COMPLETED / TOOL_* /
                                │ CONTEXT_COMPACTED 渲染
                                ▼
                          transcript / streaming / status 更新
```

- 流式输出：`MODEL_DELTA` 累积进 streaming 区，`MODEL_COMPLETED` 整段落入 transcript（payload 携带完整 content 兜底无 delta 的 Provider）
- 审批：`TuiApprovalPort.request` 把 `ApprovalAsked`（含 future）post 给 App，输入框禁用，`y/a/n` 全局键位应答并 resolve future
- 任务运行在 `run_worker(exclusive=True)` 中，`Ctrl+C` 触发 `CancelState`；空闲时 `Ctrl+C` 退出
- 审批模式：`Shift+Tab` 循环 ask ↔ auto（`PromptInput` 子类 binding 拦截，见踩坑记录）；auto 下 `ModeApprovalPort` 短路放行，审批面板不出现

## 入口分发（`main.py`）

```text
code-agent           → TUI（默认）
code-agent --cli     → CLI（原 TerminalRenderer 路径）
非 TTY（管道/CI）      → 自动降级 CLI
```

另有 `code-agent-cli` 命令直达 CLI。两种入口共享会话目录与全部数据，可随时互换。

## 组合根扩展

`AgentRuntime` 新增注入点：`approval_port` 与 `event_sink`（与既有 `provider`/`renderer` 一致）。TUI 传入 `TuiApprovalPort(app)` 与 `TuiRenderer(app)`；CLI 不传时保持原默认。循环依赖通过 `bind_runtime` 二段式装配解决（先建 App，再装 Runtime，再绑定）。

## 踩坑记录

- **属性撞名**：实例属性 `_running` 与 Textual `MessagePump` 内部状态冲突，消息泵启动后被覆盖为 True——已改名 `_task_running`
- Textual 8.x 的 `Static` 内容访问用 `.content`（非 `renderable`/`_content`）
- 消息分发基于类级 handler 注册，实例属性遮蔽无效；调试用 stderr（print 会被捕获）
- **Shift+Tab 被焦点系统抢占**：`Screen.BINDINGS` 定义了 `shift+tab → focus_previous`，App 级 BINDINGS 与 `on_key` 拦截都不可靠（前者被 Screen 先消费，后者 stop 不影响已执行的 binding）。解法：自定义 `PromptInput(Input)` 在输入框自身的 BINDINGS 上声明 `shift+tab → app.toggle_approval_mode`，focused widget 的 binding 优先级最高
- **工具测试的 cwd 依赖**：`write_file` 等工具按进程 cwd 解析相对路径，TUI 集成测试必须 `monkeypatch.chdir(tmp_path)`，否则测试会把文件写进仓库根目录
- **`_wait_for(not _task_running)` 竞态**：worker 尚未调度时谓词即满足，等待条件要写成终态（文件存在/错误文案出现），不能只看运行标志

## 测试

- pilot 驱动（`run_test`）：聊天流式往返（含 delta 分段与 COMPLETED 兜底）
- 审批流：面板出现 → 输入框禁用 → `y` 放行 → 文件落盘 → 后续回复渲染
- 斜杠命令：`/help`、`/model`（列表与切换）、`/permissions` 输出进对话流
- 审批模式：Shift+Tab 切 auto 后写文件不弹审批；用户消息分隔线；工具行紧凑摘要（完整 JSON 不泄漏）
