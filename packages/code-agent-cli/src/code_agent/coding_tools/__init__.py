"""Builtin coding tools operating on the workspace."""

from typing import Any

from .git_tools import GitDiffTool, GitStatusTool
from .list_files import ListFilesTool
from .process_tools import (
    ProcessManager,
    ProcessStatusTool,
    ReadProcessOutputTool,
    StartProcessTool,
    StopProcessTool,
    default_process_tools,
)
from .read_file import ReadFileTool
from .replace_in_file import ReplaceInFileTool
from .run_command import RunCommandTool
from .search_text import SearchTextTool
from .workspace import WorkspaceError
from .write_file import WriteFileTool

BUILTIN_CODING_TOOLS = (
    ListFilesTool,
    SearchTextTool,
    ReadFileTool,
    ReplaceInFileTool,
    WriteFileTool,
    RunCommandTool,
    GitStatusTool,
    GitDiffTool,
)


def default_coding_tools(manager: ProcessManager | None = None) -> tuple[Any, ...]:
    """Instantiate builtin coding tools with one shared process registry."""
    process_manager = manager or ProcessManager()
    finite_tools = tuple(tool_cls() for tool_cls in BUILTIN_CODING_TOOLS)
    return finite_tools + default_process_tools(process_manager)


__all__ = [
    "BUILTIN_CODING_TOOLS",
    "GitDiffTool",
    "GitStatusTool",
    "ListFilesTool",
    "ProcessManager",
    "ProcessStatusTool",
    "ReadFileTool",
    "ReadProcessOutputTool",
    "ReplaceInFileTool",
    "RunCommandTool",
    "SearchTextTool",
    "StartProcessTool",
    "StopProcessTool",
    "WriteFileTool",
    "WorkspaceError",
    "default_coding_tools",
    "default_process_tools",
]
