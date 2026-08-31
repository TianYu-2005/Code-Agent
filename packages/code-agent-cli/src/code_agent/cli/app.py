"""Interactive CLI application loop."""

import asyncio
import sys
import uuid
from typing import TYPE_CHECKING

from code_agent_core.runtime.loop import LoopEndReason
from code_agent_llm import Message, MessageRole

from .commands import HELP_TEXT, CommandResult, parse_input

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
        from code_agent_core.session import SessionStore

        runtime.session = SessionStore()
        renderer.info("已开始新会话。")
        _rebuild_runtime(runtime)
    elif command == "model":
        renderer.info(f"当前模型: {runtime.config.model}")
        if runtime.config.base_url:
            renderer.info(f"Endpoint: {runtime.config.base_url}")
    elif command == "sessions":
        renderer.info("会话管理将在持久化配置完成后提供。")
    elif command == "tree":
        branches = runtime.session.list_branches()
        if not branches:
            renderer.info("当前会话还没有消息。")
        else:
            for branch in branches:
                renderer.info(f"  分支 {branch.head_id}: {branch.message_count} 条消息")
    elif command == "compact":
        renderer.info("手动压缩尚未实现，将由上下文压缩模块提供。")
    elif command == "unknown":
        renderer.info(f"未知命令: /{argument}\n{HELP_TEXT}")
    return True


def _rebuild_runtime(runtime: "AgentRuntime") -> None:
    """Rebuild context manager after a session reset."""
    from code_agent_core.context import ContextManager, ContextPolicy, load_project_instructions

    instructions = load_project_instructions(runtime.workspace)
    policy = ContextPolicy(project_instructions=instructions) if instructions else ContextPolicy()
    runtime.context_manager = ContextManager(runtime.session, policy=policy)


async def run_app(runtime: "AgentRuntime") -> None:
    """Run the interactive REPL until the user quits."""
    renderer = runtime.renderer
    renderer.banner(str(runtime.workspace), runtime.config.model)
    while True:
        try:
            renderer.info("")
            user_input = input("\033[1m你> \033[0m").strip()
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
