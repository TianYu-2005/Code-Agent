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
│ 你 任务… / AI 回复…         │
│ ▸ ✓ success / ◇ 压缩提示    │
├────────────────────────────┤
│ streaming（流式中的回复）    │  左侧 accent 竖线，完成后落入 transcript
├────────────────────────────┤
│ approval（审批面板，条件显示）│  黄色警示，y/a/n 键位
├────────────────────────────┤
│ > 输入框                    │  圆角边框，聚焦高亮
│ ⏵ 状态 · 模型 · 提示        │  状态栏
└────────────────────────────┘
```

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

## 测试

- pilot 驱动（`run_test`）：聊天流式往返（含 delta 分段与 COMPLETED 兜底）
- 审批流：面板出现 → 输入框禁用 → `y` 放行 → 文件落盘 → 后续回复渲染
- 斜杠命令：`/help`、`/model` 输出进对话流
