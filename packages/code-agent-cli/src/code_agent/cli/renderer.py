"""Render runtime events onto the terminal."""

import sys
from typing import TextIO

from code_agent_core import RuntimeEvent, RuntimeEventType

TOOL_COLORS = {
    "success": "\033[32m",  # green
    "error": "\033[31m",  # red
    "denied": "\033[33m",  # yellow
    "timeout": "\033[33m",
    "cancelled": "\033[33m",
}
RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
BOLD = "\033[1m"


class TerminalRenderer:
    """Consume RuntimeEvent objects and print a readable transcript."""

    def __init__(
        self,
        *,
        output_stream: TextIO | None = None,
        show_tool_output: bool = True,
    ) -> None:
        self._output = output_stream or sys.stdout
        self._show_tool_output = show_tool_output
        self._current_tool: str | None = None

    def on_event(self, event: RuntimeEvent) -> None:
        """Render one runtime event."""
        if event.type is RuntimeEventType.MODEL_DELTA:
            text = event.payload.get("text", "")
            if isinstance(text, str) and text:
                self._output.write(text)
                self._output.flush()
        elif event.type is RuntimeEventType.MODEL_STARTED:
            self._current_tool = None
            self._output.write("\n")
        elif event.type is RuntimeEventType.CONTEXT_COMPACTED:
            status = str(event.payload.get("status", "compacted"))
            if status == "failed":
                self._output.write(f"{DIM}◇ 上下文压缩失败，已按预算截断旧消息{RESET}\n")
            else:
                count = event.payload.get("messages_compacted", 0)
                self._output.write(
                    f"{DIM}◇ 上下文已自动压缩（{count} 条历史消息转为摘要）{RESET}\n"
                )
        elif event.type is RuntimeEventType.TOOL_STARTED:
            self._begin_tool(event)
        elif event.type is RuntimeEventType.TOOL_COMPLETED:
            self._end_tool(event)
        elif event.type is RuntimeEventType.RUN_STARTED:
            pass
        elif event.type is RuntimeEventType.RUN_COMPLETED:
            self._output.write("\n")

    def on_tool_result(self, name: str, content: str, status: str) -> None:
        """Show bounded tool output when enabled."""
        if not self._show_tool_output or not content:
            return
        color = TOOL_COLORS.get(status, "")
        lines = content.splitlines()
        preview = "\n".join(lines[:15])
        if len(lines) > 15:
            preview += f"\n{DIM}... ({len(lines) - 15} more lines){RESET}"
        self._output.write(f"{color}{preview}{RESET}\n")

    def on_assistant_complete(self, content: str) -> None:
        """Finish an assistant message block."""
        if content:
            self._output.write("\n\n")

    def info(self, message: str) -> None:
        """Print an informational line."""
        self._output.write(f"{DIM}{message}{RESET}\n")

    def banner(self, workspace: str, model: str) -> None:
        """Print the startup banner."""
        self._output.write(
            f"{BOLD}Code Agent{RESET} {DIM}— {model} @ {workspace}{RESET}\n"
            f"{DIM}输入任务开始，/help 查看命令，Ctrl+C 取消。{RESET}\n\n"
        )

    def _begin_tool(self, event: RuntimeEvent) -> None:
        tool_call_id = event.tool_call_id or ""
        self._current_tool = tool_call_id
        self._output.write(f"\n{CYAN}▸ 工具执行中...{RESET} ")

    def _end_tool(self, event: RuntimeEvent) -> None:
        status = str(event.payload.get("status", "success"))
        color = TOOL_COLORS.get(status, "")
        self._output.write(f"{color}[{status}]{RESET}\n")
        self._current_tool = None


class RecordingSink:
    """EventSink that forwards events to a renderer."""

    def __init__(self, renderer: TerminalRenderer) -> None:
        self._renderer = renderer

    async def emit(self, event: RuntimeEvent) -> None:
        self._renderer.on_event(event)
