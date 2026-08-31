"""Builtin coding tools operating on the workspace."""

from typing import Any

from .git_tools import GitDiffTool, GitStatusTool
from .list_files import ListFilesTool
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


def default_coding_tools() -> tuple[Any, ...]:
    """Instantiate every builtin coding tool."""
    return tuple(tool_cls() for tool_cls in BUILTIN_CODING_TOOLS)


__all__ = [
    "BUILTIN_CODING_TOOLS",
    "GitDiffTool",
    "GitStatusTool",
    "ListFilesTool",
    "ReadFileTool",
    "ReplaceInFileTool",
    "RunCommandTool",
    "SearchTextTool",
    "WriteFileTool",
    "WorkspaceError",
    "default_coding_tools",
]
