"""Interactive CLI application loop."""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent_core.runtime.loop import LoopEndReason
from code_agent_llm import Message, MessageRole

from .commands import HELP_TEXT, CommandResult, parse_input
from .sessions import SessionSummary

if TYPE_CHECKING:
    from ..bootstrap import AgentRuntime


async def _run_input(runtime: "AgentRuntime", user_input: str) -> None:
    """Execute one user task through the agent loop."""
    message = Message(
        id=f"user-{uuid.uuid4().hex[:12]}",
        role=MessageRole.USER,
        content=user_input,
    )
    loop = runtime.make_loop()
    runtime.cancel_state.reset()
    runtime.cancel_state.install()
    result = await loop.run(message)
    runtime.renderer.on_assistant_complete(
        result.final_message.content if result.final_message else ""
    )
    if result.end_reason is LoopEndReason.MAX_TURNS:
        runtime.renderer.info("已达到最大轮数限制，任务中止。")
    elif result.end_reason is LoopEndReason.ERROR:
        runtime.renderer.info(f"出错: {result.error}")
    elif result.end_reason is LoopEndReason.CANCELLED:
        runtime.renderer.info("已取消。")


def _handle_command(runtime: "AgentRuntime", command: str, argument: str | None) -> bool:
    """Execute one slash command; return False to exit."""
    renderer = runtime.renderer
    if command == "help":
        renderer.info(HELP_TEXT)
    elif command == "new":
        runtime.new_session()
        renderer.info("已开始新会话。")
    elif command == "model":
        renderer.info(f"当前模型: {runtime.config.model}")
        if runtime.config.base_url:
            renderer.info(f"Endpoint: {runtime.config.base_url}")
    elif command == "sessions":
        _handle_sessions(runtime, argument)
    elif command == "tree":
        branches = runtime.session.list_branches()
        if not branches:
            renderer.info("当前会话还没有消息。")
        else:
            for branch in branches:
                renderer.info(f"  分支 {branch.head_id}: {branch.message_count} 条消息")
    elif command == "unknown":
        renderer.info(f"未知命令: /{argument}\n{HELP_TEXT}")
    return True


def _session_by_index(runtime: "AgentRuntime", number: int) -> SessionSummary | None:
    sessions = runtime.session_manager.list_sessions()
    if number < 1 or number > len(sessions):
        runtime.renderer.info(f"序号超出范围（1-{len(sessions)}）。")
        return None
    return sessions[number - 1]


def _resume_session(runtime: "AgentRuntime", number: int) -> None:
    summary = _session_by_index(runtime, number)
    if summary is None:
        return
    try:
        runtime.load_session(summary.session_id)
    except ValueError as error:
        runtime.renderer.info(f"恢复失败: {error}")
        return
    runtime.renderer.info(f"已恢复会话 {summary.session_id}（{summary.message_count} 条消息）。")


def _export_session(runtime: "AgentRuntime", number: int, target: Path | None) -> None:
    summary = _session_by_index(runtime, number)
    if summary is None:
        return
    try:
        path = runtime.session_manager.export_markdown(summary.session_id, target)
    except (ValueError, OSError) as error:
        runtime.renderer.info(f"导出失败: {error}")
        return
    runtime.renderer.info(f"已导出到 {path}")


def _handle_sessions(runtime: "AgentRuntime", argument: str | None) -> None:
    """List, resume, or export persisted sessions."""
    renderer = runtime.renderer
    parts = (argument or "").split()
    if not parts:
        sessions = runtime.session_manager.list_sessions()
        if not sessions:
            renderer.info("暂无历史会话。")
            return
        renderer.info("历史会话（/sessions <序号> 恢复，/sessions export <序号> 导出）:")
        for index, summary in enumerate(sessions, start=1):
            local = summary.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            renderer.info(f"  {index}. [{local}] {summary.message_count} 条消息 — {summary.title}")
        return
    if parts[0] == "export":
        if len(parts) < 2 or not parts[1].isdigit():
            renderer.info("用法: /sessions export <序号> [路径]")
            return
        _export_session(runtime, int(parts[1]), Path(parts[2]) if len(parts) > 2 else None)
        return
    if parts[0].isdigit():
        _resume_session(runtime, int(parts[0]))
        return
    renderer.info("用法: /sessions [序号] | /sessions export <序号> [路径]")


async def run_app(runtime: "AgentRuntime") -> None:
    """Run the interactive REPL until the user quits."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI

    renderer = runtime.renderer
    renderer.banner(str(runtime.workspace), runtime.config.model)
    session: PromptSession[str] = PromptSession()
    while True:
        try:
            renderer.info("")
            # prompt_toolkit manages the terminal itself: wide characters,
            # ANSI styling, history, and line editing all behave correctly.
            user_input = (await session.prompt_async(ANSI("\033[1m你> \033[0m"))).strip()
        except (EOFError, KeyboardInterrupt):
            renderer.info("\n再见。")
            return
        if not user_input:
            continue
        parsed: CommandResult = parse_input(user_input)
        if parsed.kind == "exit":
            renderer.info("再见。")
            return
        if parsed.kind == "command":
            keep_running = _handle_command(runtime, parsed.command or "unknown", parsed.argument)
            if not keep_running:
                return
            continue
        try:
            await _run_input(runtime, user_input)
        except KeyboardInterrupt:
            renderer.info("\n已取消。")
        except Exception as error:  # noqa: BLE001
            renderer.info(f"运行失败: {error}")


def main() -> None:
    """CLI entry point."""
    from ..bootstrap import AgentRuntime
    from ..config import load_config

    try:
        config = load_config()
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f"配置错误: {error}\n")
        raise SystemExit(1) from error
    runtime = AgentRuntime(config)
    asyncio.run(run_app(runtime))
