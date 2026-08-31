"""Inline Textual TUI for the coding agent."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from .messages import AgentEvent, ApprovalAsked, TaskFinished
from .renderer import TuiApprovalPort, TuiRenderer

if TYPE_CHECKING:
    from ..bootstrap import AgentRuntime

HELP_TEXT = """可用命令:
  /help     显示本帮助
  /new      开始新会话
  /model    显示当前模型
  /sessions 列出历史会话；/sessions <序号> 恢复
  /tree     显示当前对话树
  /rewind   回退 n 条消息并从该点分叉（/rewind <n>）
  /fork     切换到其他分支（/fork <序号>）
  /quit     退出

其他输入会作为任务发送给 Agent。Ctrl+C 取消当前运行。"""

TOOL_STATUS = {
    "success": ("✓", "green"),
    "error": ("✗", "red"),
    "denied": ("⚠", "yellow"),
    "timeout": ("⚠", "yellow"),
    "cancelled": ("·", "yellow"),
}

USER_STYLE = ("你", "bold cyan")
ASSISTANT_STYLE = ("AI", "bold magenta")


class CodeAgentApp(App[None]):
    """Inline terminal UI: scrolling transcript plus a fixed prompt area."""

    CSS_PATH = "styles.tcss"
    BINDINGS = [("ctrl+c", "cancel", "取消/退出")]

    def __init__(self) -> None:
        super().__init__()
        self._runtime: AgentRuntime | None = None
        self._stream_buffer = ""
        self._pending_approval: asyncio.Future[str] | None = None
        self._task_running = False

    # ------------------------------------------------------------------ setup

    def bind_runtime(self, runtime: AgentRuntime) -> None:
        """Attach the assembled agent runtime."""
        self._runtime = runtime

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield RichLog(id="transcript", markup=True, wrap=True, auto_scroll=True)
            yield Static(id="streaming")
            yield Static(id="approval")
            yield Input(placeholder="输入任务，/ 查看命令…", id="prompt")
            yield Static(id="status")

    def on_mount(self) -> None:
        assert self._runtime is not None
        model = self._runtime.config.model
        log = self.query_one("#transcript", RichLog)
        banner = Text()
        banner.append("Code Agent", style="bold white")
        banner.append(f"  {model}", style="dim")
        banner.append("\n输入任务开始，/help 查看命令，Ctrl+C 取消当前运行。\n", style="dim")
        log.write(banner)
        self._set_status("就绪")
        self.query_one("#prompt", Input).focus()

    # ------------------------------------------------------------------ input

    @on(Input.Submitted, "#prompt")
    async def on_prompt_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if self._task_running:
            self._transcript(Text("（任务运行中，Ctrl+C 取消后再输入）", style="dim"))
            return
        self._transcript_user(text)
        self._task_running = True
        self._set_status("思考中…")
        self.run_worker(self._run_task(text), exclusive=True)

    async def _run_task(self, text: str) -> None:
        """Run one user task through the agent loop."""
        from code_agent_llm import Message, MessageRole

        assert self._runtime is not None
        message = Message(
            id=f"user-{uuid.uuid4().hex[:12]}",
            role=MessageRole.USER,
            content=text,
        )
        loop = self._runtime.make_loop()
        self._runtime.cancel_state.reset()
        try:
            result = await loop.run(message)
            summary = ""
            if result.end_reason.value == "max_turns":
                summary = "已达到最大轮数限制，任务中止。"
            elif result.end_reason.value == "error":
                summary = f"出错: {result.error}"
        except Exception as error:  # noqa: BLE001
            summary = f"运行失败: {error}"
        self.post_message(TaskFinished(summary))

    # ---------------------------------------------------------------- events

    def on_agent_event(self, message: AgentEvent) -> None:
        event = message.event
        kind = event.type.value
        payload = event.payload
        if kind == "model_started":
            self._stream_buffer = ""
            streaming = self.query_one("#streaming", Static)
            streaming.update(Text("…", style="dim"))
            streaming.set_classes("active")
        elif kind == "model_delta":
            text = str(payload.get("text", ""))
            self._stream_buffer += text
            self.query_one("#streaming", Static).update(Text(self._stream_buffer))
        elif kind == "model_completed":
            final_text = self._stream_buffer or str(payload.get("content", ""))
            if final_text.strip():
                self._transcript_assistant(final_text)
            self._stream_buffer = ""
            streaming = self.query_one("#streaming", Static)
            streaming.update(Text(""))
            streaming.set_classes("")
        elif kind == "tool_started":
            name = str(payload.get("tool", "工具"))
            self._set_status(f"执行工具 {name}…")
        elif kind == "tool_completed":
            status = str(payload.get("status", "success"))
            icon, color = TOOL_STATUS.get(status, ("·", "white"))
            line = Text("  ▸ ", style="dim")
            line.append(icon, style=color)
            line.append(f" {status}", style="dim")
            self._transcript(line)
        elif kind == "context_compacted":
            if str(payload.get("status")) == "failed":
                self._transcript(Text("◇ 上下文压缩失败，已按预算截断旧消息", style="dim"))
            else:
                count = payload.get("messages_compacted", 0)
                self._transcript(
                    Text(f"◇ 上下文已自动压缩（{count} 条历史消息转为摘要）", style="dim")
                )
        elif kind == "run_completed":
            self._set_status("就绪")

    def on_task_finished(self, message: TaskFinished) -> None:
        self._task_running = False
        self._set_status("就绪")
        if message.summary:
            self._transcript(Text(f"\n{message.summary}", style="dim"))

    def on_approval_asked(self, message: ApprovalAsked) -> None:
        request = message.request
        lines = Text()
        lines.append("权限审批", style="bold yellow")
        lines.append(f"\n  工具: {request.call.name}", style="yellow")
        lines.append(f"\n  说明: {request.description}", style="yellow")
        for target in request.call.targets:
            lines.append(f"\n  目标: {target.resource} ({target.effect.value})", style="yellow")
        lines.append("\n  [y] 允许  [a] 本会话允许  [n] 拒绝", style="bold yellow")
        panel = self.query_one("#approval", Static)
        panel.update(lines)
        panel.set_classes("visible")
        self._pending_approval = message.future
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True

    def on_key(self, event: events.Key) -> None:
        if self._pending_approval is not None and event.key in {"y", "a", "n"}:
            future = self._pending_approval
            self._pending_approval = None
            panel = self.query_one("#approval", Static)
            panel.set_classes("")
            panel.update(Text(""))
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            prompt.focus()
            if not future.done():
                future.set_result(event.key)
            event.stop()

    def action_cancel(self) -> None:
        if self._task_running and self._runtime is not None:
            self._runtime.cancel_state.cancel()
            self._set_status("取消中…")
        else:
            self.exit()

    # --------------------------------------------------------------- commands

    def _handle_command(self, text: str) -> None:
        assert self._runtime is not None
        parts = text[1:].split()
        command = parts[0].lower() if parts else ""
        argument = parts[1] if len(parts) > 1 else None
        if command in {"quit", "exit", "q"}:
            self.exit()
            return
        if command == "help":
            self._transcript(Text(HELP_TEXT, style="dim"))
        elif command == "new":
            self._runtime.new_session()
            self._transcript(Text("已开始新会话。", style="cyan"))
        elif command == "model":
            info = f"当前模型: {self._runtime.config.model}"
            base_url = self._runtime.config.base_url
            self._transcript(Text(f"{info}\nEndpoint: {base_url}", style="dim"))
        elif command == "tree":
            branches = self._runtime.session.list_branches()
            if not branches:
                self._transcript(Text("当前会话还没有消息。", style="dim"))
            else:
                path = self._runtime.session.current_path()
                current_head = path[-1].id if path else None
                lines = [Text("分支:", style="dim")]
                for branch in branches:
                    marker = " ← 当前" if branch.head_id == current_head else ""
                    lines.append(Text(f"  {branch.message_count} 条消息{marker}", style="dim"))
                for line in lines:
                    self._transcript(line)
        elif command == "sessions":
            self._handle_sessions(argument)
        elif command == "rewind":
            self._handle_rewind(argument)
        elif command == "fork":
            self._handle_fork(argument)
        else:
            self._transcript(Text(f"未知命令: /{command}\n{HELP_TEXT}", style="dim"))

    def _handle_sessions(self, argument: str | None) -> None:
        assert self._runtime is not None
        sessions = self._runtime.session_manager.list_sessions()
        if argument is not None and argument.isdigit():
            index = int(argument)
            if index < 1 or index > len(sessions):
                self._transcript(Text(f"序号超出范围（1-{len(sessions)}）。", style="yellow"))
                return
            try:
                self._runtime.load_session(sessions[index - 1].session_id)
            except ValueError as error:
                self._transcript(Text(f"恢复失败: {error}", style="red"))
                return
            summary = sessions[index - 1]
            self._transcript(Text(f"已恢复会话（{summary.message_count} 条消息）。", style="cyan"))
            return
        if not sessions:
            self._transcript(Text("暂无历史会话。", style="dim"))
            return
        lines = [Text("历史会话（/sessions <序号> 恢复）:", style="dim")]
        for index, summary in enumerate(sessions, start=1):
            local = summary.updated_at.astimezone().strftime("%m-%d %H:%M")
            lines.append(
                Text(
                    f"  {index}. [{local}] {summary.message_count} 条 — {summary.title}",
                    style="dim",
                )
            )
        for line in lines:
            self._transcript(line)

    def _handle_rewind(self, argument: str | None) -> None:
        assert self._runtime is not None
        path = self._runtime.session.current_path()
        if not path:
            self._transcript(Text("当前会话还没有消息。", style="dim"))
            return
        if argument is None or not argument.isdigit():
            recent = path[-8:]
            lines = [Text("最近消息（/rewind <n> 回退 n 条）:", style="dim")]
            for distance, entry in enumerate(reversed(recent), start=1):
                payload = entry.payload
                content = getattr(payload, "message", None)
                role = content.role.value if content is not None else "?"
                preview = " ".join((content.content or "").split())[:30] if content else ""
                lines.append(Text(f"  -{distance}: [{role}] {preview}", style="dim"))
            for line in lines:
                self._transcript(line)
            return
        steps = int(argument)
        if steps < 1 or steps > len(path) - 1:
            self._transcript(Text(f"n 需在 1-{len(path) - 1} 之间。", style="yellow"))
            return
        target = path[len(path) - 1 - steps]
        self._runtime.session.rewind(target.id)
        self._transcript(Text(f"已回退 {steps} 条消息，后续对话将从该点分叉。", style="cyan"))

    def _handle_fork(self, argument: str | None) -> None:
        assert self._runtime is not None
        branches = self._runtime.session.list_branches()
        if not branches:
            self._transcript(Text("当前会话还没有消息。", style="dim"))
            return
        if argument is None or not argument.isdigit():
            lines = [Text("分支列表（/fork <序号> 切换）:", style="dim")]
            path = self._runtime.session.current_path()
            current_head = path[-1].id if path else None
            for index, branch in enumerate(branches, start=1):
                marker = " ← 当前" if branch.head_id == current_head else ""
                lines.append(Text(f"  {index}. {branch.message_count} 条消息{marker}", style="dim"))
            for line in lines:
                self._transcript(line)
            return
        index = int(argument)
        if index < 1 or index > len(branches):
            self._transcript(Text(f"序号需在 1-{len(branches)} 之间。", style="yellow"))
            return
        branch = branches[index - 1]
        self._runtime.session.fork(branch.head_id)
        self._transcript(Text(f"已切换到分支（{branch.message_count} 条消息）。", style="cyan"))

    # ----------------------------------------------------------------- output

    def _transcript(self, line: Text) -> None:
        log = self.query_one("#transcript", RichLog)
        log.write(line)

    def _transcript_user(self, text: str) -> None:
        log = self.query_one("#transcript", RichLog)
        log.write(Text(""))
        label, style = USER_STYLE
        line = Text()
        line.append(f"{label} ", style=style)
        line.append(text)
        log.write(line)

    def _transcript_assistant(self, text: str) -> None:
        log = self.query_one("#transcript", RichLog)
        log.write(Text(""))
        label, style = ASSISTANT_STYLE
        line = Text()
        line.append(f"{label} ", style=style)
        line.append(text)
        log.write(line)

    def _set_status(self, status: str) -> None:
        assert self._runtime is not None
        model = self._runtime.config.model
        hint = "Ctrl+C 取消" if self._task_running else "/help 命令"
        bar = Text()
        bar.append(f" ⏵ {status}", style="bold cyan")
        bar.append(f"  ·  {model}", style="dim")
        bar.append(f"  ·  {hint}", style="dim")
        self.query_one("#status", Static).update(bar)


def run_tui() -> None:
    """Assemble the runtime and launch the inline TUI."""
    import sys

    from ..bootstrap import AgentRuntime
    from ..config import load_config

    try:
        config = load_config()
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f"配置错误: {error}\n")
        raise SystemExit(1) from error

    app = CodeAgentApp()
    runtime = AgentRuntime(
        config,
        approval_port=TuiApprovalPort(app),
        event_sink=TuiRenderer(app),
    )
    app.bind_runtime(runtime)
    app.run(inline=True)
