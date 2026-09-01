"""Interactive CLI application loop."""

import asyncio
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from code_agent_core.runtime.loop import LoopEndReason
from code_agent_core.session.entries import MessageEntryPayload
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
        _handle_model(runtime, argument)
    elif command in {"permissions", "permission", "perm"}:
        _handle_permissions(runtime, argument)
    elif command == "sessions":
        _handle_sessions(runtime, argument)
    elif command == "tree":
        branches = runtime.session.list_branches()
        if not branches:
            renderer.info("当前会话还没有消息。")
        else:
            current_head = _current_head_id(runtime)
            for branch in branches:
                marker = "（当前）" if branch.head_id == current_head else ""
                renderer.info(f"  分支 {branch.head_id}: {branch.message_count} 条消息{marker}")
    elif command == "rewind":
        _handle_rewind(runtime, argument)
    elif command == "fork":
        _handle_fork(runtime, argument)
    elif command == "unknown":
        renderer.info(f"未知命令: /{argument}\n{HELP_TEXT}")
    return True


def _current_head_id(runtime: "AgentRuntime") -> str | None:
    path = runtime.session.current_path()
    return path[-1].id if path else None


def _handle_model(runtime: "AgentRuntime", argument: str | None) -> None:
    """List available models or switch to the given one."""
    renderer = runtime.renderer
    if argument:
        try:
            renderer.info(runtime.switch_model(argument))
        except ValueError as error:
            renderer.info(f"切换失败: {error}")
        return
    renderer.info("可用模型（/model <名称> 切换）:")
    config = runtime.config
    for name, profile in config.available_profiles().items():
        marker = "（当前）" if profile.model == config.model else ""
        host = profile.base_url or config.base_url
        renderer.info(f"  {name} — {profile.model} @ {host}{marker}")
    renderer.info("  也可直接用 /model <模型名>（沿用当前 endpoint）")


def _handle_permissions(runtime: "AgentRuntime", argument: str | None) -> None:
    """Show or switch the tool approval mode."""
    from ..config import ApprovalMode

    renderer = runtime.renderer
    if argument is None:
        mode = runtime.approval_mode
        hint = {
            ApprovalMode.ASK: "工具调用需要逐次确认",
            ApprovalMode.AUTO: "工具调用自动放行，不再逐次确认",
        }[mode]
        renderer.info(f"当前审批模式: {mode.value} — {hint}")
        return
    if argument not in {"ask", "auto"}:
        renderer.info("用法: /permissions [ask|auto]")
        return
    runtime.set_approval_mode(ApprovalMode(argument))
    renderer.info(f"审批模式已切换为 {argument}。")


def _handle_rewind(runtime: "AgentRuntime", argument: str | None) -> None:
    """Move the conversation head back by a number of messages."""
    renderer = runtime.renderer
    path = runtime.session.current_path()
    if not path:
        renderer.info("当前会话还没有消息。")
        return
    parts = (argument or "").split()
    if not parts or not parts[0].isdigit():
        renderer.info("当前分支最近消息（/rewind <n> 回退 n 条并从该点分叉）:")
        start = max(0, len(path) - 8)
        for index in range(len(path) - 1, start - 1, -1):
            payload = path[index].payload
            if not isinstance(payload, MessageEntryPayload):
                continue
            message = payload.message
            preview = " ".join((message.content or "").split())[:30]
            distance = len(path) - 1 - index + 1
            renderer.info(f"  -{distance}: [{message.role.value}] {preview}")
        return
    steps = int(parts[0])
    if steps < 1 or steps > len(path) - 1:
        renderer.info(f"n 需在 1-{len(path) - 1} 之间。")
        return
    target = path[len(path) - 1 - steps]
    runtime.session.rewind(target.id)
    renderer.info(f"已回退 {steps} 条消息，后续对话将从该点分叉（原消息保留在树中）。")


def _handle_fork(runtime: "AgentRuntime", argument: str | None) -> None:
    """Switch the conversation head to another branch."""
    renderer = runtime.renderer
    branches = runtime.session.list_branches()
    if not branches:
        renderer.info("当前会话还没有消息。")
        return
    parts = (argument or "").split()
    if not parts or not parts[0].isdigit():
        current_head = _current_head_id(runtime)
        renderer.info("分支列表（/fork <序号> 切换到该分支继续）:")
        for index, branch in enumerate(branches, start=1):
            marker = "（当前）" if branch.head_id == current_head else ""
            renderer.info(f"  {index}. {branch.message_count} 条消息{marker}")
        return
    index = int(parts[0])
    if index < 1 or index > len(branches):
        renderer.info(f"序号需在 1-{len(branches)} 之间。")
        return
    branch = branches[index - 1]
    runtime.session.fork(branch.head_id)
    renderer.info(f"已切换到分支（{branch.message_count} 条消息），后续对话将从该分支继续。")


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


def main(workspace: str | None = None, overrides: dict[str, str] | None = None) -> None:
    """CLI entry point."""
    from ..bootstrap import AgentRuntime
    from ..config import load_config_or_wizard

    try:
        config = load_config_or_wizard(workspace=workspace, overrides=overrides)
    except Exception as error:  # noqa: BLE001
        sys.stderr.write(f"配置错误: {error}\n")
        raise SystemExit(1) from error
    runtime = AgentRuntime(config)
    asyncio.run(run_app(runtime))
