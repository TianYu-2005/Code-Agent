"""Slash command handling for the CLI."""

from dataclasses import dataclass


@dataclass
class CommandResult:
    """Outcome of parsing one user input line."""

    kind: str  # "chat" | "exit" | "command"
    command: str | None = None
    argument: str | None = None


KNOWN_COMMANDS = ("help", "new", "model", "quit", "sessions", "tree")


def parse_input(line: str) -> CommandResult:
    """Parse one input line into a chat message or slash command."""
    stripped = line.strip()
    if not stripped:
        return CommandResult(kind="chat")
    if not stripped.startswith("/"):
        return CommandResult(kind="chat")
    parts = stripped[1:].split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else None
    if command in {"quit", "exit", "q"}:
        return CommandResult(kind="exit")
    return CommandResult(kind="command", command=command, argument=argument)


HELP_TEXT = """可用命令:
  /help     显示本帮助
  /new      开始新会话（自动持久化到 .code-agent/sessions/）
  /model    显示当前模型
  /sessions 列出历史会话；/sessions <序号> 恢复；/sessions export <序号> 导出
  /tree     显示当前对话树
  /quit     退出

其他输入会作为任务发送给 Agent。Ctrl+C 取消当前运行。"""
